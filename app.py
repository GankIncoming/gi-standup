from datetime import datetime, timedelta
import logging
import os
import asyncio
import arrow
import markdown
from bottle_ac import create_addon_app

parameter_prefix = "--"
parameter_argument_separator = "="
parameter_expiry_date = [parameter_prefix + "expiry", parameter_prefix + "expiration"]
parameter_show_expired_persistent = parameter_prefix + "show-expired"
parameter_show_all_once = parameter_prefix + "all"

db_user_key = "user"
db_status_key = "message"
db_date_key = "date"
db_expiry_key = "expiry"

option_show_expired_key = "show expired"

options = {
    option_show_expired_key: True
}

log = logging.getLogger(__name__)
app = create_addon_app(__name__,
                       plugin_key="gi-standup-dev",
                       addon_name="GI Standup Dev",
                       from_name="GI Standup",
                       base_url="https://gi-standup-dev.herokuapp.com")

app.config['MONGO_URL'] = os.environ.get("MONGO_URL", None)
app.config['REDIS_URL'] = os.environ.get("REDISTOGO_URL", None)

def init():
    @asyncio.coroutine
    def _send_welcome(event):
        client = event['client']
        yield from client.send_notification(app.addon,
            text="GI Standup was added to this room. Type '/standup I did *this*' to get started (yes, "
                 "you can use Markdown).")

    app.addon.register_event('install', _send_welcome)

app.add_hook('before_first_request', init)


# noinspection PyUnusedLocal
@app.route('/')
def capabilities(request, response):
    return {
        "links": {
            "self": app.config.get("BASE_URL"),
            "homepage": app.config.get("BASE_URL")
        },
        "key": app.config.get("PLUGIN_KEY"),
        "name": app.config.get("ADDON_NAME"),
        "description": "HipChat connect add-on that supports async standups (Gank Incoming fork)",
        "vendor": {
            "name": "Gank Incoming",
            "url": "http://gankincoming.shoutwiki.com/wiki/Main_Page"
        },
        "capabilities": {
            "installable": {
                "allowGlobal": False,
                "allowRoom": True,
                "callbackUrl": app.config.get("BASE_URL") + "/installable/"
            },
            "hipchatApiConsumer": {
                "scopes": [
                    "view_group",
                    "send_notification"
                ],
                "fromName": app.config.get("FROM_NAME")
            },
            "webhook": [
                {
                    "url": app.config.get("BASE_URL") + "/standup",
                    "event": "room_message",
                    "pattern": "^/standup(\s|$).*"
                }
            ],
        }
    }

def extract_status_parameters(status):
    parameters = {}

    while len(status) > 0:
        if status.startswith(parameter_prefix):
            parameter, _, status = status.partition(' ')
            parameter, _, argument = parameter.partition(parameter_argument_separator)
            parameters[parameter.lower()] = argument.lower()
            status = status.strip()
        else:
            break

    return status, parameters


@asyncio.coroutine
def handle_standalone_parameters(addon, client, parameters):
    if len(parameters) <= 0:
        return True

    if parameter_show_expired_persistent in parameters:
        try:
            value = string_to_bool(parameters[parameter_show_expired_persistent])
            options[option_show_expired_key] = value

            if value:
                yield from client.send_notification(addon, text = "Expired statuses WILL be shown from now on.")
            else:
                yield from client.send_notification(addon, text = "Expired statuses will NOT be shown from now on.")
        except:
            yield from client.send_notification(addon, text = "Error: invalid argument for %s." % parameter_show_expired_persistent)
        return False

    return True


@app.route('/standup', method='POST')
@asyncio.coroutine
def standup(request, response):
    body = request.json
    client_id = body['oauth_client_id']
    client = yield from app.addon.load_client(client_id)

    status = str(body['item']["message"]["message"][len("/standup"):]).strip()
    from_user = body['item']['message']['from']

    status, parameters = extract_status_parameters(status)

    if not status:
        proceed = yield from handle_standalone_parameters(app.addon, client, parameters)

        if proceed:
            yield from display_all_statuses(app.addon, client, parameters)
    elif status.startswith("@") and ' ' not in status:
        yield from display_one_status(app.addon, client, mention_name=status.strip("@"))
    else:
        yield from record_status(app.addon, client, from_user, status, parameters)

    response.status = 204


def handle_expiry_date_parameter(parameters):
    parameter_present = False
    argument = ""

    if len(parameters) > 0:
        for alias in parameter_expiry_date:
            if alias in parameters:
                parameter_present = True
                argument = parameters[alias]
                break

    if not parameter_present:
        return True, datetime.utcnow() + timedelta(days = 1)

    if len(argument) <= 0:
        print("Error: no argument for expiry parameter")
        return False, None

    # Is the argument a date? E.g.: --expiry=10.03
    if '.' in argument:
        day, _, month = argument.partition('.')
        date = datetime.utcnow().replace(day = int(day), month = int(month))

        if date < datetime.utcnow():
            return True, date.replace(year = date.year + 1)
        return True, date

    # Is the argument an interval? E.g.: --expiry=3d
    interval = string_to_timedelta(argument)

    if interval:
        return True, datetime.utcnow() + interval

    print("Error: invalid expiry argument")
    return False, None

@asyncio.coroutine
def record_status(addon, client, from_user, status, parameters):
    spec, statuses = yield from find_statuses(addon, client)
    user_mention = from_user['mention_name']
    success, expiry_date = handle_expiry_date_parameter(parameters)

    if not success:
        yield from client.send_notification(addon, text = "Error: invalid expiry argument. Status NOT recorded.")
        return

    statuses[user_mention] = {
        db_user_key: from_user,
        db_status_key: status,
        db_date_key: datetime.utcnow(),
        db_expiry_key: expiry_date
    }

    data = dict(spec)
    data['users'] = statuses

    yield from standup_db(addon).update(spec, data, upsert=True)

    yield from client.send_notification(addon, text="Status recorded.  Type '/standup' to see the full report.")

@asyncio.coroutine
def display_one_status(addon, client, mention_name):
    spec, statuses = yield from find_statuses(addon, client)

    status = statuses.get(mention_name)
    if status:
        yield from client.send_notification(addon, html=render_status(status))
    else:
        yield from client.send_notification(addon, text="No status found. "
                                                        "Type '/standup I did this' to add your own status.")


@asyncio.coroutine
def display_all_statuses(addon, client, parameters):
    show_expired = options[option_show_expired_key]

    if parameter_show_all_once in parameters:
        show_expired = True

    spec, statuses = yield from find_statuses(addon, client, show_expired = show_expired)

    if statuses:
        yield from client.send_notification(addon, html=render_all_statuses(statuses))
    else:
        yield from client.send_notification(addon, text="No status found. "
                                                        "Type '/standup I did this' to add your own status.")


def render_all_statuses(statuses):
    txt = ""
    for status in statuses.values():
        txt += render_status(status) + "<br>"
    return txt


def render_status(status):
    msg_date = arrow.get(status[db_date_key])
    expiry_date = arrow.get(status[db_expiry_key])

    message = status[db_status_key]
    html = markdown.markdown(message)
    html = html.replace("<p>", "")
    html = html.replace("</p>", "")
    name = status[db_user_key]['name']

    if is_status_expired(status):
        return "<b>EXPIRED</b>: " + "<i>{name}: {message} -- {ago} (expiry: {expiry})</i>".format(
            name = name, message = html, ago = msg_date.humanize(), expiry = expiry_date.humanize())

    return "<b>{name}</b>: {message} -- <i>{ago}, expiry: {expiry}</i>".format(name = name, message = html, ago = msg_date.humanize(),
                                                                               expiry = expiry_date.humanize())


@asyncio.coroutine
def find_statuses(addon, client, show_expired):
    spec = status_spec(client)
    data = yield from standup_db(addon).find_one(spec)
    if not data:
        statuses = {}
    else:
        statuses = data.get('users', {})
        result = {}
        for mention_name, status in statuses.items():
            if show_expired:
                result[mention_name] = status
            else:
                if not is_status_expired(status):
                    result[mention_name] = status
                else:
                    print("Filtering status from %s of date %s" % (mention_name, status['date']))

        statuses = result

    return spec, statuses

def is_status_expired(status):
    return status[db_expiry_key] is not None and status[db_expiry_key].replace(tzinfo = None) < datetime.utcnow()

def status_spec(client):
    return {
        "client_id": client.id,
        "group_id": client.group_id,
        "capabilities_url": client.capabilities_url
    }

    
def standup_db(addon):
    return addon.mongo_db.default_database['standup']


def string_to_timedelta(s):
    try:
        num = int(s[:-1])
    except:
        return None

    s = s.lower()

    if s.endswith('s'):
        return timedelta(seconds = num)
    elif s.endswith('m'):
        return timedelta(minutes = num)
    elif s.endswith('h'):
        return timedelta(hours = num)
    elif s.endswith('d'):
        return timedelta(days = num)

    return None

def string_to_bool(s):
    s = s.lower()

    if s in ["true", "t", "1"]:
        return True

    if s in ["false", "f", "0"]:
        return False

    raise ValueError("Could not convert string to bool!")


if __name__ == "__main__":
    app.run(host="", reloader=True, debug=True)

