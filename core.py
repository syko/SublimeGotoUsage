import os
import re
import time
import sublime
from . import utils
from .dep_graph import DepGraph

graphs = {}

CLASS_REGEX = {
    'regex': r'class ([^\s\(\)\[\]\{\}+*/&\|=<>,:;~-]+)',
    'group': [1]
}
FUNCTION_REGEX = {
    'regex': r'(function\s+([^\s\(\)\[\]\{\}+*/&\|=<>,:;~-]+)+.+{$)|(def\s([^\s\(\)\[\]\{\}+*/&\|=<>,:;~-]+).+:$)',
    'group': [2, 4]
}
VAR_REGEX = {
    'regex': r'(var|let|const)\s+([^\s\(\)\[\]\{\}+*/&\|=<>,:;~-]+)\s*=',
    'group': [2]
}
IMPORT_KEYWORDS = [
    'import',
    'include',
    'require'
]
IGNORED_PREFIX = [
    'import',
    'include',
    'require',
    'function',
    'const',
    'var',
    'let',
    'def',
    'class'
]
IGNORED_SUFFIX = [
    ':',
    '='
]
LOADING_FRAMES = [
    '=    ',
    ' =   ',
    '  =  ',
    '   = ',
    '    =',
    '   = ',
    '  =  ',
    ' =   ',
]

def get_item_name_on_line(line, regex):
    matches = re.match(regex['regex'], line)
    if not matches: return None
    if matches:
        return [matches.group(i) for i in regex['group'] if matches.group(i)][0]

def find_subject_name_on_current_line(view, regex):
    """
    Find a matching regex-based definition either on the current line or
    the first one going upwards from the current cursor position.
    """
    (current_region, current_line) = utils.get_current_line(view)
    return get_item_name_on_line(current_line, regex)

def find_subject_name_upwards(view, regex):
    """
    Find a matching regex-based definition either on the current line or
    the first one going upwards from the current cursor position.
    """
    (current_region, current_line) = utils.get_current_line(view)

    regions = view.find_all(regex['regex'])
    if not regions: return None

    # Find the first match going backwards from current position
    for region in reversed(regions):
      if region.b < current_region.a:
        return get_item_name_on_line(view.substr(region), regex)

    # Cursor is before any class definitions... return the first one
    return get_item_name(view.substr(regions[0]))

def find_subject_name(view):
    """
    Find a matching class/fn/var definition either on the current line or
    the first one going upwards from the current cursor position.
    """
    return (find_subject_name_on_current_line(view, CLASS_REGEX)
        or find_subject_name_on_current_line(view, FUNCTION_REGEX)
        or find_subject_name_on_current_line(view, VAR_REGEX)
        or find_subject_name_upwards(view, CLASS_REGEX)
        or find_subject_name_upwards(view, FUNCTION_REGEX)
        or find_subject_name_upwards(view, VAR_REGEX))

def is_actual_usage(line, subject):
    line_split = line.split(subject, 1)

    if line_split[0]:
        # Test for word-break before subject
        if re.match(r'[^\s()\[\]{},+*/%!;:\'\"=<>-]', line_split[0][-1]):
            return False
        # Test for definitions
        before = line_split[0].rstrip(' \t ([{}])')
        for ignored_ending in IGNORED_PREFIX:
            if before.endswith(ignored_ending):
                return False

    if line_split[1]:
        # Test for word-break after subject
        if re.match(r'[^\s()\[\]{},+*/%!;:\'\"=<>-]', line_split[1][0]):
            return False
        # Test for definitions
        after = line_split[1].rstrip(' \t ([{}])')
        for ignored_beginning in IGNORED_SUFFIX:
            if after.startswith(ignored_beginning):
                return False

    # Test for subject located inside string
    subject_pos = line.find(subject)
    strings = utils.find_strings(line)
    for range in strings:
        if range[0] < subject_pos and range[1] > subject_pos:
            return False

    return True

def goto_usage_in_file(file_path, subject):
    usage_region = None
    point = 0
    with open(file_path, 'r', encoding='utf8') as f:
        for line in f:
            if subject not in line:
                point += len(line)
                continue
            if not is_actual_usage(line, subject):
                point += len(line)
                continue
            a = line.find(subject)
            usage_region = sublime.Region(point + a, point + a + len(subject))
            break

    if usage_region:
        return {
            'name': os.path.basename(file_path),
            'path': file_path,
            'region': usage_region
        }

    return None

def find_imports_in_file(f):
    """
    Uses some broad keywords and quotation-searching to find imports.
    Only supports imports that ar between quotes and that are actual path strings.
    """
    deps = []
    found_import = False
    current_context = ['any']

    for line in f:
        line_stripped = line.strip()

        if current_context[-1] != 'comment' \
        and (len(line_stripped) and line_stripped[0] == '#' \
        or len(line_stripped) > 1 and line_stripped[:2] == '//'):
            continue # Single line comment, move on

        if current_context[-1] not in ('import', 'comment') \
        and True not in [i in line_stripped for i in IMPORT_KEYWORDS]:
            continue # No import on this line, move on

        if current_context[-1] != 'comment':
            is_comment_start = line_stripped[:2] == '/*'
            if is_comment_start:
                current_context.append('comment')
                continue

        if current_context[-1] == 'comment':
            is_comment_end = line_stripped[:2] == '*/'
            if is_comment_end:
                current_context.pop()
                continue

        if current_context[-1] == 'comment': continue

        if current_context[-1] != 'import':
            # Look for keyword
            for keyword in IMPORT_KEYWORDS:
                matches = re.search(r'(?<![^\s])(%s)\b' % keyword, line_stripped)
                if matches:
                    current_context.append('import')
                    line_stripped = line_stripped[matches.end(1):]
                    break

        if current_context[-1] == 'import':
            # Look for the next string
            matches = re.search(r'[\'\"]([^\'\"]+)[\'\"]', line_stripped)
            if matches:
                deps.append(matches.group(1))
                current_context.pop()
                continue

    return deps

def goto_usage_in_files(subject, files):
    """
    Smart approach: reads files from a list and parses them.
    """

    usage_list = []

    for file_path in files:
        try:
            usage = goto_usage_in_file(file_path, subject)
            if usage:
                usage_list.append(usage)
        except UnicodeDecodeError:
            utils.log("Failed to open file", file_name, warning=True)

    return usage_list

def goto_usage_in_folders(subject, folders):
    """
    Naive approach: reads all files and parses them.
    """

    usage_list = []

    for folder in folders:
        for root, dirs, files in os.walk(folder, True):
            files = [f for f in files if f[0] != '.' and utils.file_filter(f)]
            dirs[:] = [d for d in dirs if d[0] != '.' and utils.folder_filter(d)]
            for file_name in files:
                file_path = os.path.join(root, file_name)
                try:
                    usage = goto_usage_in_file(file_path, subject)
                    if usage:
                        usage_list.append(usage)
                except UnicodeDecodeError:
                    utils.log("Failed to open file", file_name, warning=True)

    return usage_list

def get_dependencies_in_file(file_path):
    try:
        with open(file_path, 'r', encoding='utf8') as f:
            deps = find_imports_in_file(f)
            utils.expand_aliases(deps)
            dir_path = os.path.dirname(file_path)
            utils.join_dep_paths(dir_path, deps)
            deps = list(set(utils.resolve_dep_paths(deps, utils.file_filter)))
            if file_path in deps: del deps[deps.index(file_path)]
            return deps
    except UnicodeDecodeError:
        utils.log("Failed to open file", file_path, warning=True)

def build_graph(g_to_build, folders, **kwargs):
    """Build a whole new dependency graph"""
    import random
    for folder in folders:
        for root, dirs, files in os.walk(folder, True):
            files = [f for f in files if f[0] != '.' and utils.file_filter(f)]
            dirs[:] = [d for d in dirs if d[0] != '.' and utils.folder_filter(d)]
            for file_name in files:
                file_path = os.path.join(root, file_name)
                deps = get_dependencies_in_file(file_path)
                g_to_build['graph'].add(file_path, deps)

    g_to_build['last_update'] = time.time()
    if kwargs.get('on_complete'): kwargs.get('on_complete')()

def refresh_dependencies(file_path, project_name):
    """
    Refresh the dependencies of a single file in the graph and save the graph
    to the cache if the deps have changed.
    """
    global graphs
    g = graphs.get(project_name, {})

    if not g:
        utils.log('Cannot refresh dependencies for file "%s", graph for project "%s" does not exist: loading graph' % (file_path, project_name))
        load_graph(project_name)
        return

    direct_deps = get_dependencies_in_file(file_path)
    current_deps = g['graph'].get_dependees(file_path)
    g['graph'].set(file_path, direct_deps)
    g['last_update'] = time.time()

    # Update cache if graph changed
    if g['graph'].get_dependees(file_path) != current_deps:
        utils.save_graph(g)

def load_graph(project_name):
    global graphs
    utils.log("Loading graph from cache for project %s" % project_name)
    g = utils.load_graph(project_name)
    if not g or g['graph'].num_deps == 0:
        utils.log("No graph in cache for %s: rebuilding" % project_name)
        sublime.active_window().run_command('goto_usage_build_graph', {'project_name': project_name})
    else:
        utils.log("Got %d dependencies from cache for %s" % (g['graph'].num_deps, project_name))
        if g['last_update'] < time.time() - 1000 * 60 * 60 * 24:
            utils.log("Graph older than 24h, rebuilding")
            sublime.active_window().run_command('goto_usage_build_graph', {'project_name': project_name})
        else:
            graphs[project_name] = g

def ensure_graph_exists(project_name):
    global graphs
    g = graphs.get(project_name)
    if not g:
        load_graph(project_name)

def load_all_graphs():
    project_names = utils.get_all_project_names()
    for project_name in project_names: load_graph(project_name)

open_callbacks = []

def open_usage(view, usage, is_transient = False):
    view = view.window().open_file(usage['path'], is_transient and sublime.TRANSIENT or 0)
    if view.is_loading():
        open_callbacks.append({
            'view': view,
            'callback': lambda view, cb: show_usage(view, usage)
        })
    else:
        show_usage(view, usage)

def show_usage(view, usage):
    sel = view.sel()
    sel.clear()
    sel.add(usage['region'])
    sublime.set_timeout(lambda: view.show(usage['region']), 100)
