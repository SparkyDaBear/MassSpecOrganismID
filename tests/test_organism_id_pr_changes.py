import importlib.util
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("organism_id", ROOT / "src/OrganismID.py")
ORGANISM = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ORGANISM)


class ContaminantTests(unittest.TestCase):
    def test_il_equivalence_and_human_substring_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fasta = root / "contaminants.fasta"
            fasta.write_text(">standard\nMILIPEPTIDEK\n")
            (root / "SkinHairContaminants.fasta").write_text(
                ">skin\nAAAAASUBSTRINGPEPTIDEAAAAA\n")
            frame = pd.DataFrame({
                "peptide": ["MLLLPEPTLDEK", "SUBSTRINGPEPTIDE", "UNRELATEDPEPTIDE"],
                "score": [0.9, 0.9, 0.9],
            })
            nonhuman = ORGANISM.remove_contaminants(frame.copy(), str(fasta))
            human = ORGANISM.remove_contaminants(frame.copy(), str(fasta), human_sample=True)
            self.assertEqual(nonhuman["peptide"].tolist(), ["UNRELATEDPEPTIDE"])
            self.assertEqual(human["peptide"].tolist(),
                             ["SUBSTRINGPEPTIDE", "UNRELATEDPEPTIDE"])

    def test_empty_input(self):
        with tempfile.TemporaryDirectory() as directory:
            fasta = Path(directory) / "contaminants.fasta"
            fasta.write_text(">standard\nMILIPEPTIDEK\n")
            frame = pd.DataFrame(columns=["peptide", "score"])
            self.assertTrue(ORGANISM.remove_contaminants(frame, str(fasta)).empty)


class TaxonomyPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        root = Path(cls.temporary.name)
        submodule = ROOT / "submodules/peptonizer2000"
        destination = root / "peptonizer/peptonizer"
        destination.mkdir(parents=True)
        for name in ("fetch_peptides_and_filter_taxa.py", "unipept_communicator.py",
                     "ncbi_ranks.py", "request_manager.py"):
            content = subprocess.check_output([
                "git", "-C", str(submodule), "show",
                "HEAD:peptonizer/peptonizer/" + name,
            ])
            (destination / name).write_bytes(content)
        for name in ("unipept_nontryptic_species_rollup.patch", "pride_genus_rollup.patch"):
            patch_file = ROOT / "patches/peptonizer2000" / name
            subprocess.run(["git", "apply", str(patch_file)], cwd=root, check=True)
        cls.package_name = "_organism_pr_test_peptonizer"
        package = types.ModuleType(cls.package_name)
        package.__path__ = [str(destination)]
        sys.modules[cls.package_name] = package
        cls.filter_module = importlib.import_module(
            cls.package_name + ".fetch_peptides_and_filter_taxa")
        cls.communicator = importlib.import_module(cls.package_name + ".unipept_communicator")
        cls.ranks = cls.communicator.NCBI_RANKS

    @classmethod
    def tearDownClass(cls):
        for name in list(sys.modules):
            if name == cls.package_name or name.startswith(cls.package_name + "."):
                del sys.modules[name]
        cls.temporary.cleanup()

    def mock_communicator(self, peptide_taxa, species, descendants):
        ranks = self.ranks

        class MockCommunicator:
            def get_taxa_for_peptides(self, peptides):
                return {peptide: list(taxa) for peptide, taxa in peptide_taxa.items()}

            def get_descendants_for_taxa(self, taxa, rank):
                self.queries = set(taxa)
                return descendants

            def get_lineages_for_taxa(self, taxa):
                result = {}
                for taxid in taxa:
                    lineage = [None] * len(ranks)
                    lineage[ranks.index("genus")] = 99
                    lineage[ranks.index("species")] = species.get(taxid)
                    result[taxid] = lineage
                return result

        return MockCommunicator()

    def test_species_ancestor_recovery(self):
        mock = self.mock_communicator({"PEPTIDE": [112]}, {111: 111, 112: 111}, [111])
        with patch.dict(os.environ, {"PEPTONIZER_GENUS_ROLLUP_TAXA": ""}):
            result = self.filter_module.fetch_peptides_and_filter_taxa(
                ["PEPTIDE"], "112", "species", mock)
        self.assertEqual(result, {"PEPTIDE": [111]})
        self.assertIn(111, mock.queries)

    def test_approved_pride_genus_assignment_and_ambiguity(self):
        mock = self.mock_communicator(
            {"PEPTIDE": [222]}, {111: 111, 222: 222, 333: 333}, [111, 333])
        with patch.dict(os.environ, {"PEPTONIZER_GENUS_ROLLUP_TAXA": "111"}):
            result = self.filter_module.fetch_peptides_and_filter_taxa(
                ["PEPTIDE"], "111", "species", mock)
        self.assertEqual(result, {"PEPTIDE": [111]})
        with patch.dict(os.environ, {"PEPTONIZER_GENUS_ROLLUP_TAXA": "111,333"}):
            result = self.filter_module.fetch_peptides_and_filter_taxa(
                ["PEPTIDE"], "111,333", "species", mock)
        self.assertEqual(result, {"PEPTIDE": []})

    def test_nontryptic_request(self):
        response = unittest.mock.Mock(status_code=200)
        response.json.return_value = [{"peptide": "PEPTIDE", "taxa": [111]}]
        with patch.object(self.communicator.RequestManager, "perform_post_request",
                          return_value=response) as request:
            self.communicator.UnipeptCommunicator().get_taxa_for_peptides(["PEPTIDE"])
        self.assertIs(request.call_args.args[1]["tryptic"], False)


if __name__ == "__main__":
    unittest.main()
