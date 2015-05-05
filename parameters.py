import json

name_key = "name"
aliases_key = "aliases"
with_status_key = "usable_with_status"
without_status_key = "usable_without_status"
show_statuses_key = "show_statuses"
short_desc_key = "short_description"
long_desc_key = "long_description"

argument_separator = "="

class Parameter(object):
    def __init__(self, name, aliases, with_status, without_status, show_statuses, short_desc, long_desc):
        self.name = name
        self.aliases = aliases
        self.with_status = with_status
        self.without_status = without_status
        self.show_statuses = show_statuses
        self.short_desc = short_desc
        self.long_desc = long_desc

    def has_alias(self, alias):
        return alias in self.aliases

def parse_json(filename):
    parameters = {}

    # TODO

    return parameters

def dict_to_parameter(d):
    return Parameter(d[name_key], d[aliases_key], d[with_status_key], d[without_status_key], d[show_statuses_key], d[short_desc_key], d[long_desc_key])
