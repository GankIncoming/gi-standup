from datetime import datetime, timedelta
import logging
import os
import asyncio
import arrow
import markdown
from bottle_ac import create_addon_app

parameter_prefix = "--"
parameter_argument_separator = "="
parameter_expiry_date = parameter_prefix + "expiry"

log = logging.getLogger(__name__)
app = create_addon_app(__name__,
                       plugin_key="gi-standup",
                       addon_name="GI Standup",
                       from_name="Standup",
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
    parameters = []

    while len(status) > 0:
        if status.startswith(parameter_prefix):
            parameter, _, status = status.partition(' ')
            parameters.append(parameter)
            status = status.strip()
        else:
            break

    return status, parameters


@asyncio.coroutine
def handle_parameters(addon, client, parameters):
    pass

@app.route('/standup', method='POST')
@asyncio.coroutine
def standup(request, response):
    body = request.json
    client_id = body['oauth_client_id']
    client = yield from app.addon.load_client(client_id)

    status = str(body['item']["message"]["message"][len("/standup"):]).strip()
    from_user = body['item']['message']['from']

    if not status:
        yield from display_all_statuses(app.addon, client)
    elif status.startswith("@") and ' ' not in status:
        yield from display_one_status(app.addon, client, mention_name=status.strip("@"))
    else:
        status, parameters = extract_status_parameters(status)

        if len(status) > 0:
            yield from record_status(app.addon, client, from_user, status, parameters)
        else:
            yield from handle_parameters(app.addon, client, parameters)

    response.status = 204


@asyncio.coroutine
def handle_expiry_date_parameter(parameters):
    argument = ""

    for parameter in parameters:
        if parameter.startswith("--expiry"):
        #if parameter.startswith(parameter_expiry_date):
            _, _, argument = parameter.partition("=")
            break

    if len(argument) <= 0:
        print("Error: no argument")
        return None

    # Is the argument a date? E.g.: --expiry=10.03
    if '.' in argument:
        day, _, month = argument.partition('.')
        date = datetime.utcnow().replace(day = int(day), month = int(month))

        if date < datetime.utcnow():
            return date.replace(year = date.year + 1)
        return date

    # Is the argument an interval? E.g.: --expiry=3d
    try:
        num = int(argument[:-1])
    except:
        print("Error: could not convert argument to integer")
        return None

    if argument.endswith('s'):
        return datetime.utcnow() + timedelta(seconds = num)
    elif argument.endswith('m'):
        return datetime.utcnow() + timedelta(minutes = num)
    elif argument.endswith('h'):
        return datetime.utcnow() + timedelta(hours = num)
    elif argument.endswith('d'):
        return datetime.utcnow() + timedelta(days = num)

    print("Error: invalid argument")
    return None

@asyncio.coroutine
def record_status(addon, client, from_user, status, parameters):
    spec, statuses = yield from find_statuses(addon, client)
    user_mention = from_user['mention_name']
    expiry_date = handle_expiry_date_parameter(parameters)

    statuses[user_mention] = {
        "user": from_user,
        "message": status,
        "date": datetime.utcnow(),
        "expiry": expiry_date
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
def display_all_statuses(addon, client):
    spec, statuses = yield from find_statuses(addon, client)

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
    msg_date = arrow.get(status['date'])
    expiry_date = arrow.get(status["expiry"])

    message = status['message']
    html = markdown.markdown(message)
    html = html.replace("<p>", "")
    html = html.replace("</p>", "")
    name = status['user']['name']

    to_print = "<b>{name}</b>: {message} -- <i>{ago}, expiry: {expiry}</i>".format(name = name, message = html, ago = msg_date.humanize(),
                                                                           expiry = expiry_date.humanize())

    if status["expiry"] is not None and status["expiry"] < datetime.utcnow():
        return "<s>" + to_print + "</s>"
    return to_print


@asyncio.coroutine
def find_statuses(addon, client):
    spec = status_spec(client)
    data = yield from standup_db(addon).find_one(spec)
    if not data:
        statuses = {}
    else:
        statuses = data.get('users', {})
        result = {}
        for mention_name, status in statuses.items():
            result[mention_name] = status
            #if status['date'].replace(tzinfo=None) > datetime.utcnow()-timedelta(days=3):
            #    result[mention_name] = status
            #else:
            #    print("Filtering status from %s of date %s" % (mention_name, status['date']))

        statuses = result

    return spec, statuses


def status_spec(client):
    return {
        "client_id": client.id,
        "group_id": client.group_id,
        "capabilities_url": client.capabilities_url
    }

    
def standup_db(addon):
    return addon.mongo_db.default_database['standup']

if __name__ == "__main__":
    app.run(host="", reloader=True, debug=True)
