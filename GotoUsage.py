import os
import threading
import time
import sublime, sublime_plugin
from . import utils
from . import core
from .dep_graph import DepGraph

class RetValThread(threading.Thread):
    """
    Thread that accepts a `on_complete` callback that gets the return value of the
    target function passed to it.
    """
    def __init__(self, *args, **kwargs):
        self._on_complete = kwargs['on_complete']
        del kwargs['on_complete']
        super().__init__(*args, **kwargs)

    def run(self):
        ret = self._target(*self._args, **self._kwargs)
        self._on_complete(ret)

class GotoUsageCommand(sublime_plugin.TextCommand):

    def run(self, edit):

        window = sublime.active_window()
        project_folders = window.folders()

        # Find wrapping class definition
        # If no class found, find wrapping function definition

        subject = core.find_subject_name(self.view)

        if not subject:
            sublime.status_message("GotoUsage: Could not find class/function name to search for")
            return

        def on_complete(found_usage_list):
            if not len(found_usage_list):
                sublime.status_message("GotoUsage: Could not find class/function/var '%s'" % subject)
                return

            def on_item_selected(index):
                core.open_usage(self.view, found_usage_list[index])

            def on_item_highlighted(index):
                core.open_usage(self.view, found_usage_list[index], True)

            # Shorten the paths by removing the project path from them
            for project_folder in project_folders:
                path = os.path.abspath(project_folder)
                for usage in found_usage_list:
                    usage['display_path'] = "%s:%d" % (usage['path'].replace(path, '').strip('/\\'), usage['line_nr'])

            menu_list = [i['display_path'] for i in found_usage_list]
            window.show_quick_panel(menu_list, on_item_selected, 0, 0, on_item_highlighted)

        if utils.get_setting('disable_dep_graph', False):
            RetValThread(
                target=core.get_usages_in_folders,
                args=[subject, project_folders],
                on_complete=on_complete
            ).start()
        else:
            g = core.graphs.get(utils.get_project_name(window), {})
            if not g:
                core.load_graph(utils.get_project_name(window))
                # See if got it from cache
                g = core.graphs.get(utils.get_project_name(window), {})
                if not g: return

            current_file = self.view.file_name()
            files = g['graph'].get_dependants(current_file)

            # Append current file and make sure it's the last one
            if current_file in files:
                del files[files.index(current_file)]
            files.append(current_file)

            RetValThread(
                target=core.get_usages_in_files,
                args=[subject, files],
                on_complete=on_complete
            ).start()

building_graphs = []

class GotoUsageClearCachesCommand(sublime_plugin.WindowCommand):
    def run(self):
        if building_graphs:
            sublime.status_message("GotoUsage: Please wait while the current dependency graph has finished building")
            return
        utils.clear_caches()
        core.graphs = {}
        sublime.status_message("GotoUsage: Cleared all dependency graphs")

class GotoUsageBuildGraphCommand(sublime_plugin.WindowCommand):
    def run(self, project_name = None):
        global building_graphs

        if project_name in building_graphs: return

        g = {
            'last_update': None,
            'graph': DepGraph()
        }

        project_folders = self.window.folders()
        project_name = project_name or utils.get_active_project_name()
        self.window.active_view().erase_status('GotoUsage')

        building_graphs.append(project_name)
        self.loading_frame = 0
        self.loading_start = time.time()

        def erase_status():
            self.window.active_view().erase_status('GotoUsage')

        def show_progress():
            global building_graphs
            if project_name not in building_graphs or project_name != building_graphs[0]: return
            self.window.active_view().set_status('GotoUsage', '[%s] GotoUsage: %d dependencies' % (core.LOADING_FRAMES[self.loading_frame], g['graph'].num_deps))
            self.loading_frame = (self.loading_frame + 1) % len(core.LOADING_FRAMES)
            if time.time() - self.loading_start > 60: # Taking too much time, bail
                erase_status()
                del building_graphs[building_graphs.index(project_name)]
            else:
                sublime.set_timeout(show_progress, 100)

        def on_complete():
            global building_graphs
            if project_name in building_graphs:
                del building_graphs[building_graphs.index(project_name)]
            core.graphs[project_name] = g
            utils.save_graph(g, project_name)
            utils.log('Built graph with %d dependencies' %  g['graph'].num_deps)
            self.window.active_view().set_status('GotoUsage', 'GotoUsage complete: found %d dependencies' % g['graph'].num_deps)
            sublime.set_timeout(erase_status, 4000)

        show_progress()

        threading.Thread(target=core.build_graph, args=[g, project_folders], kwargs={
            "on_complete": on_complete
        }).start()

class FileOpenListener(sublime_plugin.EventListener):
    """
    Runs file opening callbacks when a file has finished opening.
    Used to scroll the viewport to the usage and highlight it after the async open operation.
    """
    def on_load(self, view):
        for cb in core.open_callbacks:
            if cb['view'] == view:
                cb['callback'](view, cb)
                del core.open_callbacks[core.open_callbacks.index(cb)]
                break

class FileSaveListener(sublime_plugin.EventListener):
    """Refresh the dependencies of a file upon saving."""
    def on_post_save_async(self, view):
        if utils.file_filter(view.file_name()):
            core.refresh_dependencies(view.file_name(), utils.get_project_name(view))
