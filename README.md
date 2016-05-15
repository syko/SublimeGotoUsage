# SublimeGotoUsage

The opposite of Goto Definition.

With the cursor in a class defintion, invoke Goto Usage and get a list of all the places where the class is used.
You can easily browse through the list and jump to the specific use case.

Default keyboard mapping: `cmd+alt+o` / `ctrl+alt+o`

## Installing

Clone this repository into your Sublime Packages directory (Package Control support coming soon).

## Usage

Simply press `cmd+alt+o` (`ctrl+alt+o`) when looking at a class definition.

You can also run these commands manually:
- `Goto Usage`
- `Goto Usage: Rebuild Dependency Graph`

By default Goto Usage builds a dependency graph of the current project and only traverses upstream files when looking
for "usages". Usages are matched by name (does not work with renamed imports!)

Dependency graph is built by looking for import statements in the code. These statements are assumed to be nodejs-style
file/folder paths. Works with es6 `import`, commonjs `require` and even `include` (??) statements.

It should be relatively easy to adapt this to other languages as the imports are parsed very loosely. Officially supports
only **javascript** and **coffeescript** as of now.

**However!** You can disable the dependency graph by setting the `disable_dep_graph` to `false`. This makes `Goto Usage`
switch to the naive approach and traverses **all** project files and matches usages by name in all files. This
can be a time-intensive operation (based on how large your project is) so it's **very important** to configure
`file_extensions` and `excluded_folders` properly to minimize the number of files parsed!

## Commands

- `Goto Usage`: Takes the current class definition (cursor inside class definition) and finds where this class is used
  within the current project (usage matched by name: does not work with names imports!)
- `Goto Usage: Rebuild Dependency Graph`: Fully rebuild the dependency graph of the current project. Dependency graph is
  built once and then cached & updated on each file save so it should keep itself up to date unless you add/edit files outside
  Sublime Text. This is where this command may come in handy.

## Configuration

Example configuration:

```json
{
  "file_extensions": [".js", ".coffee", ".jsx"],
  "excluded_folders": ["node_modules", "dist"],
  "my_project": {
    "alias": {
      "components": "/fullpath/components/",
      "utils": "/fullpath/utils/"
    },
    "file_extensions": [".js", ".coffee", ".jsx"],
    "excluded_folders": ["node_modules", "dist", "build", "tmp", ".tmp"],
    "disable_dep_graph": false
  }
}
```

You can have default settings as well as project-based settings by scoping the setting with the name of the project.
The name of the project to use in the configuration is the name of your project file without the `.sublime-project` extension.

Configuration options:
- `disable_dep_graph`: Disable the dependency graph and switch to naive mode instead.
- `alias`: Add aliases that might occur within imports (so `require 'foo/bar.js'` translates to `require 'some/path/bar.js'`)
- `file_extensions`: List of file extensions to consider. (default: `[".js", ".coffee", ".jsx"]`)
- `excluded_folders`: List of folders to exclude. (default: `["node_modules", "dist", "build", "tmp", ".tmp"]`)

## Notes on dependency graph

The dependency graph tries to remove false negatives as much as possible so:
- Paths that point to a directory are expanded to all files within the given directory. So
  `require 'my/path'` adds all files under directory `path` as dependencies.
- Paths that point to a file but lack the extension are expanded to files that start with the same basename.
  So `require 'my/component'` expands to something like `my/component.js`


## Contributing

Issues are welcome, so a PRs.
