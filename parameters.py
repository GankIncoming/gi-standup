import json

name_key = "name"
aliases_key = "aliases"
with_status_key = "usable_with_status"
without_status_key = "usable_without_status"
show_statuses_key = "show_statuses"
short_desc_key = "short_description"
long_desc_key = "long_description"

argument_separator = "="
prefix = "--"
prefix_short = "-"


class Parameter(object):
    def __init__(self, name, aliases, with_status, without_status, show_statuses, short_desc, long_desc):
        self.name = name
        self.aliases = aliases
        self.with_status = with_status
        self.without_status = without_status
        self.show_statuses = show_statuses
        self.short_desc = short_desc
        self.long_desc = long_desc
        self.short_help_str = self.name + ": " + self.short_desc
        self.long_help_str = self.short_help_str + "\n" + self.long_desc + "\nAliases: " + ", ".join(self.aliases)

        default_alias = prefix + self.name
        if default_alias not in self.aliases:
            self.aliases.insert(0, default_alias)


    def has_alias(self, alias):
        return alias in self.aliases


class ParameterCollection(object):
    def __init__(self):
        self.alias_to_name = {}
        self.parameters = {}

    def get(self, alias):
        if alias in self.parameters:
            return self.parameters[alias]

        return self.parameters[self.alias_to_name[alias]]

    def add(self, parameter):
        for alias in parameter.aliases:
            if alias in self.alias_to_name:
                raise ValueError("Alias %s already exists in the dictionary!" % alias)

        if parameter.name in self.parameters:
            raise ValueError("Parameter %s already exists in the dictionary!" % parameter.name)

        for alias in parameter.aliases:
            self.alias_to_name[alias] = parameter.name

        self.parameters[parameter.name] = parameter

    def has(self, alias):
        return alias in self.alias_to_name

    def __getitem__(self, item):
        return self.get(item)

    def __contains__(self, item):
        return self.has(item)

def parse_json(filename):
    parameters = ParameterCollection()
    file = open(filename, 'r')
    json_dict = json.load(file)
    file.close()

    for p_dict in json_dict["parameters"]:
        parameters.add(dict_to_parameter(p_dict))

    return parameters


def dict_to_parameter(d):
    return Parameter(d[name_key], d[aliases_key], d[with_status_key], d[without_status_key], d[show_statuses_key], d[short_desc_key],
                     d[long_desc_key])
