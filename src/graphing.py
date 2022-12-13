# import pickle
from collections import defaultdict
from pathlib import Path

import networkx as nx
from pyvis.network import Network
from tree_sitter_languages import get_parser

# Top 20 programming languages and their extensions
languages = {
    'c': 'c',
    'cpp': 'cpp',
    'csharp': 'cs',
    'go': 'go',
    'java': 'java',
    'javascript': 'js',
    'php': 'php',
    'python': 'py',
    'ruby': 'rb',
    'rust': 'rs',
    'scala': 'scala',
    'swift': 'swift',
    'typescript': 'ts',
    'bash': 'sh',
    'clojure': 'clj',
    'elixir': 'ex',
    'erlang': 'erl',
    'haskell': 'hs',
    'lua': 'lua',
    'perl': 'pl',
}

languages = {v: k for k, v in languages.items()}

function_identifiers = ['function_definition', 'function_item']

# Open the pickle file in binary mode
# with open('../EleutherAI_o_gpt-neox.pk', 'rb') as file:
#     # Load the objects from the file
#     data = pickle.load(file)
#     print(data)


class ParsedFile:
    def __init__(
        self,
        filepath: Path,
        language_name: str,
        imported_modules: list,
        function_definitions: list,
        function_calls: list,
        module_call_map: dict,
        local_call_graph: nx.DiGraph,
        project_connections: list = None,
    ) -> None:
        self.filepath = filepath
        self.language_name = language_name
        self.imported_modules = imported_modules
        self.function_definitions = function_definitions
        self.function_calls = function_calls
        self.node_call_map = module_call_map
        self.local_call_graph = local_call_graph
        self.project_connections = project_connections  # on file level
        # TODO: Make it on function call level too


class ImportedModule:
    def __init__(
        self, module_base_name: str, module_name_as: str = None, imported_objects: list = None
    ) -> None:
        self.module_base_name = module_base_name
        self.module_name_as = module_name_as
        self.imported_objects = imported_objects

    @classmethod
    def from_tree_node(cls, tree_node):
        module_base_name = None
        module_name_as = None
        imported_objects = None
        if tree_node.type == 'import_from_statement':
            module_base_name = tree_node.named_children[0].text.decode('ascii')
            imported_objects = [
                ch.text.decode('ascii') for ch in tree_node.named_children[1:]
            ]

        elif tree_node.type == 'import_statement':
            for child in tree_node.children:
                if child.type == 'dotted_name':
                    module_base_name = child.text.decode('ascii')
                elif child.type == 'aliased_import':
                    module_base_name = child.named_children[0].text.decode('ascii')
                    module_name_as = child.named_children[1].text.decode('ascii')
        return cls(module_base_name, module_name_as, imported_objects)

    def __str__(self) -> str:
        if self.module_name_as is None:
            return self.module_base_name
        return f'{self.module_base_name} as {self.module_name_as}'


def get_fun_name(node):
    name = [ch.text.decode('ascii') for ch in node.children if ch.type == 'identifier'][0]
    return name


def get_function_definitions(tree_node):
    functions = []
    for child in tree_node.children:
        if child.type in function_identifiers:
            functions.append(child)
        if child.children is not None:
            functions += get_function_definitions(child)
    return functions


def get_calls_in_node(tree_node):
    node_calls = []
    for child in tree_node.children:
        if child.type == 'call':
            node_calls.append(child)
        if child.children is not None:
            node_calls += get_calls_in_node(child)
    return node_calls


def function_call_to_text(function_call):
    function_call_text = ''
    for child in function_call.children:
        if child.type == 'attribute':
            function_call_text += child.text.decode('ascii')
            break
        elif child.type == 'identifier':
            function_call_text += child.text.decode('ascii')
            break
    return function_call_text


def function_to_call_graph(
    function,
    full_graph,
    function_definition_names=None,
    module_name=None,
    imported_modules=None,
):
    function_name = get_fun_name(function)
    function_calls = get_calls_in_node(function)
    add_module_name = function_definition_names is not None and module_name is not None
    if add_module_name:
        function_name = module_name + '.' + function_name
    full_graph.add_node(function_name, content=function.parent.text)
    for function_call in function_calls:
        function_call_name = function_call_to_text(function_call)
        if add_module_name:
            if function_call_name in function_definition_names:
                function_call_name = module_name + '.' + function_call_name
            elif imported_modules is not None:
                matches_imported_module = match_function_call_to_imported_module(
                    function_call_name, imported_modules
                )
                if matches_imported_module is not None:
                    function_call_name = (
                        matches_imported_module.module_base_name + '.' + function_call_name
                    )
        full_graph.add_node(function_call_name)
        full_graph.add_edge(function_name, function_call_name)


def build_call_graph(
    tree_node,
    cur_graph,
    all_function_definitions=None,
    module_name=None,
    imported_modules=None,
):
    function_definitions = get_function_definitions(tree_node)
    function_definition_names = [
        get_fun_name(function_definition) for function_definition in function_definitions
    ]
    for function_definition in function_definitions:
        function_to_call_graph(
            function_definition,
            cur_graph,
            function_definition_names,
            module_name,
            imported_modules,
        )


def build_node_call_map(tree_node):
    call_map = defaultdict(list)
    calls = get_calls_in_node(tree_node)
    for call in calls:
        if call.named_child_count >= 2 and call.named_children[0].named_child_count >= 2:
            module_name = call.named_children[0].named_children[0].text.decode('ascii')
            function_name = call.named_children[0].named_children[1].text.decode('ascii')
            call_map[module_name].append(function_name)
    return call_map


def get_imported_modules(tree_node):
    used_modules = []
    for child in tree_node.children:
        if child.type == 'import_statement':
            used_modules.append(ImportedModule.from_tree_node(child))
        if child.type == 'import_from_statement':
            used_modules.append(ImportedModule.from_tree_node(child))
        if child.children is not None:
            used_modules += get_imported_modules(child)
    return used_modules


def parse_file(filepath, file_bytes=None, module_name=None):
    # Get the language name from the file extension
    language_name = languages[filepath.suffix[1:]]
    # Get the parser for the language
    parser = get_parser(language_name)
    if file_bytes is None:
        file_bytes = bytes(open(filepath, 'r').read(), 'utf-8')
    # Parse the file
    tree = parser.parse(file_bytes)
    local_call_graph = nx.DiGraph()
    # TODO: Traverse the tree only once
    module_call_map = build_node_call_map(tree.root_node)  # noqa
    function_definitions = get_function_definitions(tree.root_node)
    function_calls = get_calls_in_node(tree.root_node)
    imported_modules = get_imported_modules(tree.root_node)
    build_call_graph(
        tree.root_node,
        local_call_graph,
        function_definitions,
        module_name,
        imported_modules,
    )

    # renamed_call_graph = rename_call_graph_nodes(local_call_graph, filepath.stem)

    parsed_file = ParsedFile(
        filepath=filepath,
        language_name=language_name,
        imported_modules=imported_modules,
        function_definitions=function_definitions,
        function_calls=function_calls,
        module_call_map=module_call_map,
        local_call_graph=local_call_graph,
    )
    return parsed_file


def parsed_file_is_imported(imported_modules, parsed_file):
    for imported_module in imported_modules:
        if parsed_file.filepath.stem in imported_module.module_base_name.split('.'):
            return True
    return False


def match_function_call_to_imported_module(function_call, imported_modules):
    for imported_module in imported_modules:
        if function_call in imported_module.module_base_name.split('.'):
            return imported_module
        if imported_module.imported_objects is not None:
            for imported_object in imported_module.imported_objects:
                if function_call == imported_object:
                    return imported_module
    return None


def merge_graphs(graphs):
    merged_graph = nx.DiGraph()
    for graph in graphs:
        merged_graph = nx.compose(merged_graph, graph)
    return merged_graph


def build_file_level_graph(parsed_files):
    file_level_graph = nx.DiGraph()
    for filename, parsed_file in parsed_files.items():
        file_level_graph.add_node(filename)

    for filename, parsed_file in parsed_files.items():  # noqa
        for filename_b, parsed_file_b in parsed_files.items():
            if filename == filename_b:
                continue
            if parsed_file_is_imported(parsed_file.imported_modules, parsed_file_b):
                file_level_graph.add_edge(filename, filename_b)
    full_function_call_graph = merge_graphs(
        [parsed_file.local_call_graph for parsed_file in parsed_files.values()]
    )

    return file_level_graph, full_function_call_graph


def make_node_call_graph_for_project(
    local_project_dir=None, github_filelist=None, max_depth=3
):
    parsed_files = {}
    using_github = False
    using_local = False
    if local_project_dir is not None:
        filepaths = local_project_dir.glob('**/*')
        project_depth = len(local_project_dir.parts)
        using_local = True
    elif github_filelist is not None:
        filepaths = github_filelist
        project_depth = 1
        using_github = True
    else:
        Exception('Must provide either a local project directory or a github filelist')

    for filepath_pre in filepaths:
        if using_local:
            filepath = filepath_pre
        elif using_github:
            filepath = Path(filepath_pre.path)

        if filepath.is_dir():
            continue
        depth = len(filepath.parts)
        if depth - project_depth > max_depth:
            continue
        if filepath.suffix[1:] in languages:
            module_name = '.'.join(filepath.parts[project_depth:]).replace(
                filepath.suffix, ''
            )
            if using_local:
                parsed_file = parse_file(filepath, file_bytes=None, module_name=module_name)
            elif using_github:
                parsed_file = parse_file(
                    filepath, file_bytes=filepath_pre.decoded_content, module_name=module_name
                )
            parsed_files['/'.join(filepath.parts[project_depth:])] = parsed_file

    file_level_graph, full_function_call_graph = build_file_level_graph(parsed_files)

    return file_level_graph, full_function_call_graph


def get_network_from_gh_filelist(github_filelist):
    file_level_graph, full_function_call_graph = make_node_call_graph_for_project(
        github_filelist=github_filelist
    )
    nt_files = Network(directed=True, bgcolor='#f2f3f4', height=1080, width=1080)
    nt_files.from_nx(file_level_graph)

    nt_all = Network(directed=True, bgcolor='#f2f3f4', height=1080, width=1080)
    nt_all.from_nx(full_function_call_graph)

    return nt_all, nt_files


def main():
    # Get the graph for the file
    file_level_graph, full_function_call_graph = make_node_call_graph_for_project(
        Path('..') / 'test_files' / 'repo-review'
    )
    # graph = make_node_graph_for_file('main.py')
    # graph = make_node_graph_for_file('test_files/rs_test_0.rs')
    # graph = make_node_graph_for_file('../test_files/repo-review/main.py')
    # Print the graph

    # nx.draw_networkx(file_level_graph)
    # plt.show()
    #
    # nx.draw_networkx(full_function_call_graph)
    # plt.show()
    nt_files = Network(directed=True, bgcolor='#f2f3f4', height=1080, width=1080)
    nt_files.from_nx(file_level_graph)

    nt_all = Network(directed=True, bgcolor='#f2f3f4', height=1080, width=1080)
    nt_all.from_nx(full_function_call_graph)

    nt_files.show('files.html')

    nt_all.show('all.html')


if __name__ == '__main__':
    # Get the graph for the file
    main()