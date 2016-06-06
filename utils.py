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
    raise Exception("Infinite loop protection kicked in (i=%d). Something's buggy" % i)

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
    project_settings = settings.get(get_active_project_name() or 'no_project', {})
    return project_settings.get(name, settings.get(name, default))

def log(*args, **kwargs):
    error = kwargs.get('error', False)
    warning = kwargs.get('warning', False)
    if get_setting('verbose_logging') or error or warning:
        print('GotoUsage%s:' % (error and ' Error' or warning and ' Warning' or ''), *args)

def file_filter(file_name):
    """Return True if the file passes the filter."""
    extensions = get_setting('file_extensions', [])
    if not extensions: return True
    return True in [file_name.endswith(ext) for ext in extensions]

def folder_filter(folder_name):
    """Return True if the folder passes the filter."""
    excluded_folders = get_setting('excluded_folders', [])
    folder_name = folder_name.rstrip(os.sep) + os.sep
    return True not in [exc in folder_name for exc in excluded_folders]

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

def join_dep_path(dir_path, path):
    "Join dir_path with path and normalize it"
    return os.path.abspath(os.path.join(dir_path, path))

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
        pass
    return file_list

def get_cache_dir():
    path = os.path.join(sublime.cache_path(), 'GotoUsage')
    if not os.path.exists(path):
        os.mkdir(path)
    return path

def get_dep_cache_path(project_name):
    return os.path.join(get_cache_dir(), '%s-cache.json' % project_name)

def load_graph(project_name):
    """Load graph from cache to `graph`"""
    path = get_dep_cache_path(project_name)
    log("Attempting to load graph for '%s' from %s" % (project_name, path))
    try:
        f = open(path, 'r', encoding='utf8')
        data = json.loads(f.read())
        f.close()
        graph = DepGraph()
        graph.set_data(data['graph'])
        return {
            'last_update': data['last_update'],
            'graph': graph
        }
    except IOError:
        return None

def save_graph(g, project_name):
    """Save current graph to cache"""
    path = get_dep_cache_path(project_name)
    log("Saving graph for '%s' to cache: %s" % (project_name, path))
    try:
        f = open(path, 'w')
        data = {
            'last_update': g['last_update'],
            'graph': g['graph'].get_data()
        }
        f.write(json.dumps(data, separators=(',',':')))
        f.close()
    except IOError as e:
        log("Failed to save dependency graph: %s" % e.message, error=True)

def clear_caches():
    files = get_files_in_dir(get_cache_dir())
    for file_path in files:
        (parent_dir, file_name) = os.path.split(file_path)
        log("Removing cache file %s" % file_path)
        os.remove(file_path)

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

def resolve_dep_paths(paths, from_path, file_filter_fn = lambda x: True, folder_filter_fn = lambda x: True):
    """
    Try to fix all paths that don't appear to point to actual files.

    Paths that point to a directory => expand to all files within the given directory
    Paths that point to a file but lack the extension => expand to files that start with the same basename
    Paths that can't be resolved to anything => prepend each 'root' path and try again
    Paths that still can't be resolved to anything => ignore dat shit (must be some lib import)
    """
    resolved_paths = []

    def add_path(path):
        resolved_paths.append(path)

    def expand_path(path):
        """
        Expand the absolute path `path`
        return 2 bools: (found_file, passed_filter)
        """

        # Add file path
        if isfile(path):
            if not file_filter_fn(path): return (True, False)
            if not folder_filter_fn(os.path.split(path)[0]): return (True, False)
            add_path(path)
            return (True, True)

        # Add files in dir
        if isdir(path):
            if not folder_filter_fn(path): return (True, False)
            file_paths = [f for f in get_files_in_dir(path, False)]
            file_paths_filtered = [f for f in file_paths if file_filter_fn(f)]
            if not len(file_paths_filtered): return [len(file_paths) > 0, False]
            for file_path in file_paths_filtered:
                add_path(os.path.join(path, file_path))
            return (True, True)

        # Add matching filenames
        (parent_dir, file_substr) = os.path.split(path.rstrip(os.sep))
        all_files_in_dir = get_files_in_dir(parent_dir, False)
        if not folder_filter_fn(parent_dir): return (len(all_files_in_dir) > 0, False)
        matching_files_in_dir = [f for f in all_files_in_dir if os.path.basename(f).startswith(file_substr)]
        matching_files_filtered = [f for f in matching_files_in_dir if file_filter_fn(f)]

        if not matching_files_filtered: return [len(matching_files_in_dir) > 0, False]
        for path in matching_files_filtered:
            add_path(path)
        return (True, True)

        return (False, False)

    for path in paths:
        roots = [from_path] + get_setting('root', [])
        found_any_file = False
        for root in roots:
            full_path = join_dep_path(root, path)
            (found_file, passed_filter) = expand_path(full_path)
            if found_file and not passed_filter:
                log("Found a dependency for path but ignoring due to file and folder filters: '%s'" % full_path)
            found_any_file = found_any_file or found_file
            if found_file and passed_filter: break

        # Warn when file was not found and the reason wasn't filtering
        if not found_any_file:
            log("Could not resolve import %s. Did you forget to add an alias? (import from %s)" % (path, from_path), warning=True)

    return resolved_paths
