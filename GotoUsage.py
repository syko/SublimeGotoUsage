import os
import threading
import time
import sublime, sublime_plugin
from . import utils
from . import core

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
            sublime.status_message("Could not find class/function name to search for")
            return

        def on_complete(found_usage_list):
            if not len(found_usage_list):
                sublime.status_message("Could not find class/function '%s'" % subject)
                return

            def on_item_selected(index):
                core.open_usage(self.view, found_usage_list[index])

            def on_item_highlighted(index):
                core.open_usage(self.view, found_usage_list[index], True)

            # Shorten the paths by removing the project path from them
            for project_folder in project_folders:
                path = os.path.abspath(project_folder)
                for usage in found_usage_list:
                    usage['display_path'] = usage['path'].replace(path, '').strip('/\\')

            menu_list = [i['display_path'] for i in found_usage_list]
            window.show_quick_panel(menu_list, on_item_selected, 0, 0, on_item_highlighted)

        if utils.get_setting('disable_dep_graph', False):
            RetValThread(
                target=core.goto_usage_in_folders,
                args=[subject, project_folders],
                on_complete=on_complete
            ).start()
        else:
            current_file = self.view.file_name()
            RetValThread(
                target=core.goto_usage_in_files,
                args=[subject, core.graph.get_dependants(current_file) + [current_file]],
                on_complete=on_complete
            ).start()

building_graph = False

class GotoUsageBuildGraphCommand(sublime_plugin.TextCommand):

    def run(self, edit):
        global building_graph

        if building_graph: return

        core.graph.clear()

        window = sublime.active_window()
        project_folders = window.folders()
        self.view.erase_status('GotoUsage')

        building_graph = True
        self.loading_frame = 0
        self.loading_start = time.time()

        def erase_status():
            self.view.erase_status('GotoUsage')

        def show_progress():
            global building_graph
            if not building_graph: return
            self.view.set_status('GotoUsage', '[%s] GotoUsage: %d dependencies' % (core.LOADING_FRAMES[self.loading_frame], core.graph.num_deps))
            self.loading_frame = (self.loading_frame + 1) % len(core.LOADING_FRAMES)
            if time.time() - self.loading_start > 60: # Taking too much time, bail
                erase_status()
                building_graph = False
            else:
                sublime.set_timeout(show_progress, 100)

        def on_complete():
            global building_graph
            building_graph = False
            self.view.set_status('GotoUsage', 'GotoUsage complete: found %d dependencies' % core.graph.num_deps)
            sublime.set_timeout(erase_status, 4000)

        show_progress()

        threading.Thread(target=core.build_graph, args=[project_folders], kwargs={
            "on_complete": on_complete
        }).start()

class FileOpenListener(sublime_plugin.EventListener):
    """Runs file opening callbacks when a file has finished opening.

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
            core.refresh_dependencies(view.file_name())

core.load_graph()