
class DepGraph:
    """
    Simple dependency graph implementation.
    Nodes are strings.

    glossary:
    dependant imports the dependee
    """

    def __init__(self, loop_limit = 50):
        self.loop_limit = loop_limit
        self.num_deps = 0
        self.clear()

    def clear(self):
        self.forward_graph = {}
        self.backward_graph = {}
        self.num_deps = 0

    def add(self, dependant, dependee):

        # In case of list passed as dependee, add all
        if type(dependee) in [list, tuple]:
            for d in dependee:
                self.add(dependant, d)
            return

        self.num_deps += 1

        # Store dependant -> dependee
        if dependant not in self.forward_graph:
            self.forward_graph[dependant] = []
        if dependee not in self.forward_graph[dependant]:
            self.forward_graph[dependant].append(dependee)
        # Store dependee -> dependant
        if dependee not in self.backward_graph:
            self.backward_graph[dependee] = []
        if dependant not in self.backward_graph[dependee]:
            self.backward_graph[dependee].append(dependant)

    def set(self, dependant, dependee):

        # Clear list

        if dependant in self.forward_graph:
            self.num_deps -= len(self.forward_graph[dependant])
            self.forward_graph[dependant] = []

        # Add new deps
        # In case of list passed as dependee, set all
        if type(dependee) in [list, tuple]:
            for d in dependee:
                self.add(dependant, d)
        else:
            self.add(dependant, dependee)

    def get_dependants(self, dependee):
        return self._traverse_graph(self.backward_graph, dependee)

    def get_dependees(self, dependant):
        return self._traverse_graph(self.forward_graph, dependant)

    def get_data(self):
        return {
            'forward': self.forward_graph,
            'backward': self.backward_graph
        }

    def set_data(self, data):
        self.forward_graph = data.get('forward', {})
        self.backward_graph = data.get('backward', {})
        self.num_deps = len([dep for deps in self.forward_graph.values() for dep in deps])

    def _traverse_graph(self, graph, subject):
        results = []
        subjects = [subject] # For loop instead of recursion
        for i in range(self.loop_limit):
            current_results = []
            for s in subjects:
                current_results.extend([i for i in graph.get(s, []) if i not in results])
            if not current_results: break
            subjects = current_results
            results.extend(current_results)
        results = list(set(results))
        return results


if __name__ == "__main__":

    # Test

    graph = DepGraph()
    graph.add('a', 'b')
    graph.add('a', 'c')
    graph.add('b', 'c')
    graph.add('b', 'd')

    assert sorted(graph.get_dependees('a')) == ['b', 'c', 'd']
    assert sorted(graph.get_dependees('b')) == ['c', 'd']
    assert sorted(graph.get_dependants('c')) == ['a', 'b']
    assert sorted(graph.get_dependants('d')) == ['a', 'b']
