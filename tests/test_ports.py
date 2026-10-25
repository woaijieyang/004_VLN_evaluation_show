import socket,unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from process_show.ports import choose,candidates

class PortTests(unittest.TestCase):
    def test_occupied_port_gets_different_binding_for_second_project(self):
        with socket.socket() as first:
            first.bind(('127.0.0.1',0));occupied=first.getsockname()[1]
            second=choose('/project-B',occupied)
            self.assertNotEqual(second,occupied)
            with socket.socket() as binding:
                binding.bind(('127.0.0.1',second))
                third=choose('/project-C',occupied)
                self.assertNotIn(third,(occupied,second))
    def test_pair_checks_internal_transport_port(self):
        with socket.socket() as busy:
            busy.bind(('127.0.0.1',0));internal=busy.getsockname()[1]
            if internal<=1024:self.skipTest('ephemeral range')
            public=choose('/pair',internal-1,pair=True)
            self.assertNotEqual(public,internal-1)
            with socket.socket() as a,socket.socket() as b:
                a.bind(('127.0.0.1',public));b.bind(('127.0.0.1',public+1))
    def test_project_candidates_stable_and_distinct(self):
        a=list(next(iter(candidates('/one',8765))) for _ in range(2))
        self.assertEqual(a,[8765,8765])
        x=candidates('/one',8765);y=candidates('/two',8765)
        next(x);next(y);self.assertNotEqual(next(x),next(y))
    def test_invalid_ports_rejected(self):
        for value in (0,80,65535,True):
            with self.assertRaises(ValueError):choose('x',value)
