def get_module_name(module_name):
    if len(module_name) > 3:
        return module_name[:-3].replace(' ', '_')