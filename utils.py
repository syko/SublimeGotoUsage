import os
import re
import sublime
import json
from .dep_graph import DepGraph

STRING_DELIMITERS = ['"', "'", '`']

def infinite(max_iterations = 200):
    "A generator for use in place of `while True`. Comes with friendly infinite loop protection."
    i = 0
    while i < max_iterations:
        yield True
        i += 1
    raise Exception("Infinite loop protection kicked in (i=%d). Fix your crappy loop!" % i)

def get_current_line(view):
    "Returns the first highlighted line region and line contents"
    line_region = view.line(view.sel()[0])
    line = view.substr(line_region)
    return (line_region, line)

def find_strings(input):
    """
    Find string literals in string

    >>> find_strings('"foo" + `bar` + 123, \\\\\\'foo \\\\"b\\\\\\\\"ar\\\\\\'')
    [(0, 4), (8, 12), (32, 37)]
    >>> find_strings('boo\\\\"foo"bar')
    [(8, 12)]
    """

    string_ranges = []

    def count_backslashes(input, from_pos):
        num_backslashes = 0
        i = from_pos
        while i >= 0:
            if input[i] == '\\':
                num_backslashes += 1
                i -= 1
            else:
                break
        return num_backslashes

    for delim in STRING_DELIMITERS:
        start = -1
        for i in infinite():
            first = input.find(delim, start + 1)
            if first == -1: break # to next delim
            start = first + 1
            if count_backslashes(input, first - 1) % 2 != 0: continue # Esacped: to next delim
            next = first
            for i in infinite():
                next = input.find(delim, next + 1)
                if next == -1: break # to next delim
                if count_backslashes(input, next - 1) % 2 == 0: break # Not escaped: stop looking

            if next == -1: # ??? unmatches quotations
                string_ranges.append((first, len(input)))
                break # to next delim
            start = next
            string_ranges.append((first, next))

    return sorted(string_ranges)


def get_project_name(view_or_window):
    try:
        window = view_or_window.window()
    except AttributeError:
        window = view_or_window
    vars = window.extract_variables()
    return vars.get('project_base_name')

def get_active_project_name():
    "Return the currently active project name"
    vars = sublime.active_window().extract_variables()
    return vars.get('project_base_name')

def get_all_project_names():
    "Return a list of all open project names"
    windows = sublime.windows()
    varses = [w.extract_variables() for w in windows]
    project_names = [v.get('project_base_name') for v in varses]
    return project_names

def get_setting(name, default = None):
    settings = sublime.load_settings('GotoUsage.sublime-settings')
    project_settings = settings.get(get_active_project_name(), {})
    if project_settings:
        return project_settings.get(name, default)
    else:
        return settings.get(name, default)

def log(*args, **kwargs):
    error = kwargs.get('error', False)
    warning = kwargs.get('warning', False)
    if get_setting('verbose_logging') or error or warning:
        print('GotoUsage%s:' % (error and ' Error' or warning and ' Warning' or ''), *args)

def file_filter(file_name):
    """Return True if the file passes the filter."""
    extensions = get_setting('file_extensions')
    if not extensions: return True
    return True in [file_name.endswith(ext) for ext in extensions]

def folder_filter(folder_name):
    """Return True if the folder passes the filter."""
    excluded_folders = get_setting('excluded_folders')
    return folder_name not in excluded_folders

def expand_aliases(paths):
    "Replace all aliases in paths with the actual path"
    aliases = get_setting('alias', {})
    for alias, alias_path in aliases.items():
        for i in range(len(paths)):
            if not paths[i].startswith(alias): continue
            path_split = paths[i].split(os.sep, 1)
            if path_split[0] == alias:
                paths[i] = os.path.join(alias_path, len(path_split) > 1 and path_split[1] or '')

    return paths

def join_dep_paths(dir_path, paths):
    "Join dir_path with each path and normalize it"
    for i in range(len(paths)):
        paths[i] = os.path.abspath(os.path.join(dir_path, paths[i]))
    return paths

def get_files_in_dir(path, recursive = True):
    "Return a list of all files in a directory (and its subdirectories if recursive = True)"
    file_list = []
    try:
        if not recursive:
            return [os.path.join(path, f) for f in os.listdir(path) if os.path.isfile(os.path.join(path, f))]
        for root, dirs, files in os.walk(path, True):
            for f in files:
                file_list.append(os.path.join(root, f))
    except FileNotFoundError as e:
        log("File Not Found: %s Did you forget to add an alias?" % e.filename, warning=True)
    return file_list

def get_dep_cache_path(project_name):
    return os.path.join(sublime.cache_path(), 'GotoUsage-cache-%s.json' % project_name)

def load_graph(project_name):
    """Load graph from cache to `graph`"""
    path = get_dep_cache_path(project_name)
    try:
        f = open(path, 'r')
        data = json.loads(f.read())
        f.close()
        graph = DepGraph()
        graph.set_data(data)
        return graph
    except IOError:
        return None

def save_graph(graph, project_name):
    """Save current graph to cache"""
    path = get_dep_cache_path(project_name)
    try:
        f = open(path, 'w')
        f.write(json.dumps(graph.get_data(), separators=(',',':')))
        f.close()
    except IOError as e:
        log("Failed to save dependency graph: %s" % e.message, error=True)

isfile_cache = {}
def isfile(path):
    """Memoized version of os.path.isfile"""
    global isfile_cache
    if path not in isfile_cache:
        isfile_cache[path] = os.path.isfile(path)
    return isfile_cache[path]

isdir_cache = {}
def isdir(path):
    """Memoized version of os.path.isdir"""
    global isdir_cache
    if path not in isdir_cache:
        isdir_cache[path] = os.path.isdir(path)
    return isdir_cache[path]

def resolve_dep_paths(paths, file_filter_fn = lambda x: True):
    """
    Try to fix all paths that don't appear to point to actual files.

    Paths that point to a directory => expand to all files within the given directory
    Paths that point to a file but lack the extension => expand to files that start with the same basename
    Paths that can't be resolved to anything => ignore dat shit (must be some lib import)
    """
    resolved_paths = []

    def add_path(path):
        if not file_filter_fn(path): return
        resolved_paths.append(path)

    for path in paths:

        # Add file path

        if isfile(path):
            add_path(path)
            continue

        # Add files in dir

        if isdir(path):
            for file_path in get_files_in_dir(path, False):
                add_path(os.path.join(path, file_path))
            continue

        # Add matching filenames

        (parent_dir, file_substr) = os.path.split(path.rstrip(os.sep))
        all_files_in_dir = get_files_in_dir(parent_dir, False)
        matching_files_in_dir = [f for f in all_files_in_dir if os.path.basename(f).startswith(file_substr) and file_filter_fn(f)]

        if matching_files_in_dir:
            for path in matching_files_in_dir:
                add_path(path)
            continue

    return resolved_paths
