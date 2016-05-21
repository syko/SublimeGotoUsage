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

SINGLE_LINE_COMMENT = ['#', '//']
MULTI_LINE_COMMENT_START = ['/*']
MULTI_LINE_COMMENT_END = ['/*']
SINGLE_LINE_IMPORT_RE = r'\b(import|require|include)[^\[:.].*[\'\"][^\'\"]+[\'\"].*$'
MULTI_LINE_IMPORT_START_RE = r'\b(import|require|include)\b[\s()\[\]{}]*$'
MULTI_LINE_IMPORT_END_RE = r'^[)}\]](\s*from.+)?$'

C_ANY                 = 0b111111111
C_CODE                = 0b000000001
C_IMPORT              = 0b000011110
C_SINGLE_IMPORT       = 0b000000010
C_MULTI_IMPORT_START  = 0b000000100
C_MULTI_IMPORT        = 0b000001000
C_MULTI_IMPORT_END    = 0b000010000
C_COMMENT             = 0b111100000
C_SINGLE_COMMENT      = 0b000100000
C_MULTI_COMMENT_START = 0b001000000
C_MULTI_COMMENT       = 0b010000000
C_MULTI_COMMENT_END   = 0b100000000

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
IGNORED_BEFORE = [
    'import',
    'include',
    'require'
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
        # Filter out imports
        for ignored_before in IGNORED_BEFORE:
            if ignored_before in before:
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

def parse_lines(f, yield_context=C_ANY):
    """
    Generator for looping over lines in a file while ignoring comments.
    Works like a state-machine emitting only the necessary states (contexts).
    Continues to next line as early as possible for speeeed.
    """
    current_context = [C_CODE] # Default context when nothing else mathces
    line_nr = 0
    line_start = 0
    for line_unstripped in f:
        line = line_unstripped.strip('\t ;')
        line_nr += 1

        # Handle single-line comments
        if not current_context[-1] & C_MULTI_COMMENT:
            is_single_line_comment = True in (line.startswith(c) for c in SINGLE_LINE_COMMENT)
            if is_single_line_comment:
                if yield_context & C_COMMENT: yield (line_start, line_nr, line)
                line_start += len(line_unstripped)
                continue

        # Handle end of multi-line comment
        if current_context[-1] & C_MULTI_COMMENT:
            is_comment_end = True in (line.startswith(c) for c in MULTI_LINE_COMMENT_END)
            if is_comment_end:
                if yield_context & C_MULTI_COMMENT_END: yield (line_start, line_nr, line)
                line_start += len(line_unstripped)
                current_context.pop()
                continue # Kinda assuming nothing comes after `*/` here

        # Handle start of multi-line comment
        if not current_context[-1] & C_MULTI_COMMENT:
            is_comment_start = True in (line.startswith(c) for c in MULTI_LINE_COMMENT_START)
            if is_comment_start:
                current_context.append(C_MULTI_COMMENT)
                if yield_context & C_MULTI_COMMENT_START: yield (line_start, line_nr, line)
                line_start += len(line_unstripped)
                continue

        # Handle single-line import
        if not current_context[-1] & C_MULTI_IMPORT:
            is_single_line_import = re.search(SINGLE_LINE_IMPORT_RE, line)
            if is_single_line_import:
                if yield_context & C_SINGLE_IMPORT: yield (line_start, line_nr, line)
                line_start += len(line_unstripped)
                continue

        # Handle end of import
        if current_context[-1] & C_MULTI_IMPORT:
            is_import_end = re.search(MULTI_LINE_IMPORT_END_RE, line)
            if is_import_end:
                if yield_context & C_MULTI_IMPORT_END: yield (line_start, line_nr, line)
                current_context.pop()
                line_start += len(line_unstripped)
                continue

        # Handle start of multi-line import
        if not current_context[-1] & C_MULTI_IMPORT:
            is_import_start = re.search(MULTI_LINE_IMPORT_START_RE, line)
            if is_import_start:
                current_context.append(C_MULTI_IMPORT)
                if yield_context & C_MULTI_IMPORT_START: yield (line_start, line_nr, line)
                line_start += len(line_unstripped)
                continue

        # No context switch detected: yield current context for current line

        if yield_context & current_context[-1]:
            yield (line_start, line_nr, line)
            line_start += len(line_unstripped)
def find_imports_in_file(f):
    """
    Uses some broad keywords and quotation-searching to find imports.
    Only supports imports that are between quotes and that are actual path strings.
    """
    deps = []
    for (line_start, line_nr, line) in parse_lines(f, C_SINGLE_IMPORT | C_MULTI_IMPORT | C_MULTI_IMPORT_END):
        paths = re.findall(r'[\'\"]([^\'\"]+)[\'\"]', line)
        if paths: deps.append(paths[-1])
    return deps

def get_usages_in_file(file_path, subject):
    usage_regions = []
    with open(file_path, 'r', encoding='utf8') as f:
        for (line_start, line_nr, line) in parse_lines(f, C_CODE):
            if subject not in line: continue
            if not is_actual_usage(line, subject): continue
            offset = line.find(subject)
            usage_regions.append({
                'line_nr': line_nr,
                'path': file_path,
                'region': sublime.Region(line_start + offset, line_start + offset + len(subject))
            })

    return usage_regions

def get_usages_in_files(subject, files):
    """
    Smart approach: reads files from a list and parses them.
    """

    usage_list = []

    for file_path in files:
        try:
            usages = get_usages_in_file(file_path, subject)
            usage_list.extend(usages)
        except UnicodeDecodeError:
            utils.log("Failed to open file", file_name, warning=True)

    return usage_list

def get_usages_in_folders(subject, folders):
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
                    usages = get_usages_in_file(file_path, subject)
                    usage_list.extend(usages)
                except UnicodeDecodeError:
                    utils.log("Failed to open file", file_name, warning=True)

    return usage_list

def get_dependencies_in_file(file_path):
    try:
        with open(file_path, 'r', encoding='utf8') as f:
            deps = find_imports_in_file(f)
            utils.expand_aliases(deps)
            dir_path = os.path.dirname(file_path)
            deps = list(set(utils.resolve_dep_paths(deps, dir_path, utils.file_filter, utils.folder_filter)))
            if file_path in deps: del deps[deps.index(file_path)]
            return deps
    except UnicodeDecodeError:
        utils.log("Failed to open file", file_path, warning=True)

def build_graph(g_to_build, folders, **kwargs):
    """Build a whole new dependency graph"""
    for folder in folders:
        for root, dirs, files in os.walk(folder, True):
            files = [f for f in files if f[0] != '.' and utils.file_filter(f)]
            dirs[:] = [d for d in dirs if d[0] != '.' and utils.folder_filter(os.path.join(root, d))]
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
