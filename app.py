from datetime import datetime, timedelta
import logging
import os
import asyncio
import arrow
import markdown
from bottle_ac import create_addon_app
import parameters

db_user_key = "user"
db_status_key = "message"
db_date_key = "date"
db_expiry_key = "expiry"

param_expiry_name = "expiry"
param_help_name = "help"
param_all_name = "all"
param_no_expired_name = "no-expired"
param_show_expired_name = "show-expired"

param_filename = "parameters.json"
param_collection = parameters.ParameterCollection()

log = logging.getLogger(__name__)
app = create_addon_app(__name__,
                       plugin_key="gi-standup-dev",
                       addon_name="GI Standup Dev",
                       from_name="GI Standup",
                       base_url="https://gi-standup-dev.herokuapp.com")

app.config['MONGO_URL'] = os.environ.get("MONGO_URL", None)
app.config['REDIS_URL'] = os.environ.get("REDISTOGO_URL", None)

def init():
    global param_collection

    @asyncio.coroutine
    def _send_welcome(event):
        client = event['client']
        yield from client.send_notification(app.addon,
            text="GI Standup was added to this room. Type '/standup I did *this*' to get started (yes, "
                 "you can use Markdown).")

    app.addon.register_event('install', _send_welcome)
    param_collection = parameters.parse_json(param_filename)
    assign_parameter_handlers()

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

def assign_parameter_handlers():
    global param_collection

    param_collection[param_show_expired_name].handler = handle_show_expired
    param_collection[param_help_name].handler = handle_help_parameter

def extract_status_parameters(status):
    status_params = {}

    while status.startswith(parameters.prefix_short):
        parameter, _, temp = status.partition(' ')
        parameter, _, argument = parameter.partition(parameters.argument_separator)

        if parameter not in param_collection:
            print("%s not in parameter collection." % parameter)
            break

        status_params[parameter] = argument
        status = temp.strip()

    return status, status_params

@asyncio.coroutine
def handle_standalone_parameters(addon, client, status_params):
    if len(status_params) <= 0:
        return True

    # Use the 1st valid parameter
    for param_alias in status_params:
        param = param_collection[param_alias]

        if not param.without_status:
            continue

        if hasattr(param, "handler"):
            yield from param.handler(addon, client, status_params[param_alias])

        return param.show_statuses

    # Don't proceed if there were parameters but not usable without a status.
    yield from client.send_notification(addon, text = "Error: the parameters present require a status.")
    return False


@asyncio.coroutine
def handle_show_expired(addon, client, argument):
    try:
        value = string_to_bool(argument)
        options = yield from options_db(addon, client)

        if not options:
            options = {}

        options[param_show_expired_name] = value

        yield from save_to_db(addon, client, statuses = None, options = options)

        if value:
            yield from client.send_notification(addon, text = "Expired statuses WILL be shown from now on.")
        else:
            yield from client.send_notification(addon, text = "Expired statuses will NOT be shown from now on.")
    except:
        yield from client.send_notification(addon, text = "Error: invalid argument for --expiry.")

@asyncio.coroutine
def handle_help_parameter(addon, client, argument):
    if len(argument) > 0:
        if argument not in param_collection:
            yield from client.send_notification(addon, text = "Error: invalid argument for --help.")
        else:
            param = param_collection[argument]
            long_desc = ("<b>Usage</b>: %s<br>" % param.long_desc) if len(param.long_desc) > 0 else ""
            txt = "<b>{name}</b>: {desc}<br>" \
                  "{long_desc}" \
                  "<b>Aliases</b>: {aliases}".format(name = param.name, desc = param.short_desc, long_desc = long_desc,
                                                     aliases = ", ".join(param.aliases))
            yield from client.send_notification(addon, html = txt)
        return

    param_name_list = sorted([k for k in param_collection.parameters])
    txt = ""

    for name in param_name_list:
        param = param_collection[name]
        txt += "<b>{name}</b>: {desc}<br>".format(name = param.name, desc = param.short_desc)

    yield from client.send_notification(addon, html = txt)

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

def handle_expiry_parameter(status_params):
    parameter_present = False
    argument = ""

    if len(status_params) > 0:
        for alias in param_collection.get(param_expiry_name).aliases:
            if alias in status_params:
                parameter_present = True
                argument = status_params[alias]
                break

    if not parameter_present:
        return True, datetime.utcnow() + timedelta(days = 1) # TODO make it configurable

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
def record_status(addon, client, from_user, status, status_params):
    spec, statuses = yield from find_statuses(addon, client, show_expired = True)
    user_mention = from_user['mention_name']
    success, expiry_date = handle_expiry_parameter(status_params)

    if not success:
        yield from client.send_notification(addon, text = "Error: invalid expiry argument. Status NOT recorded.")
        return

    statuses[user_mention] = {
        db_user_key: from_user,
        db_status_key: status,
        db_date_key: datetime.utcnow(),
        db_expiry_key: expiry_date
    }

    yield from save_to_db(addon, client, statuses = statuses, options = None)

    yield from client.send_notification(addon, text="Status recorded.  Type '/standup' to see the full report.")


@asyncio.coroutine
def save_to_db(addon, client, statuses, options, delete = False):
    if statuses is None and options is None:
        return

    if statuses is None and not delete:
        statuses = yield from find_statuses(addon, client, show_expired = True)

    if options is None and not delete:
        options = yield from options_db(addon, client)

    spec = status_spec(client)
    data = dict(spec)
    data["users"] = statuses
    data["options"] = options

    yield from standup_db(addon).update(spec, data, upsert = True)

@asyncio.coroutine
def display_one_status(addon, client, mention_name):
    spec, statuses = yield from find_statuses(addon, client, show_expired = True)

    status = statuses.get(mention_name)
    if status:
        yield from client.send_notification(addon, html=render_status(status))
    else:
        yield from client.send_notification(addon, text="No status found. "
                                                        "Type '/standup I did this' to add your own status.")


@asyncio.coroutine
def display_all_statuses(addon, client, status_params):
    options = yield from options_db(addon, client)
    show_expired = True

    if options is not None and param_show_expired_name in options:
        show_expired = options[param_show_expired_name]

    # TODO figure out something better because this looks like shit
    found = False
    for alias in param_collection[param_all_name].aliases:
        if alias in status_params:
            show_expired = True
            found = True
            break

    if not found:
        for alias in param_collection[param_no_expired_name].aliases:
            if alias in status_params:
                show_expired = False
                break

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

    return "<b>{name}</b>: {message} -- <i>{ago} (expiry: {expiry})</i>".format(name = name, message = html, ago = msg_date.humanize(),
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

@asyncio.coroutine
def options_db(addon, client):
    spec = status_spec(client)
    data = yield from standup_db(addon).find_one(spec)
    if data:
        return data.get("options", {})
    return None

def string_to_timedelta(s):
    try:
        num = int(s[:-1])
    except:
        return None

    if num <= 0:
        print("Error: Time interval less or equal to zero!")
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

    if s in ["true", "t", "1", "yes"]:
        return True

    if s in ["false", "f", "0", "no"]:
        return False

    raise ValueError("Could not convert string to bool!")

if __name__ == "__main__":
    app.run(host="", reloader=True, debug=True)
