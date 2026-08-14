import ast
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

class RemoveTelegramForkV57Tests(unittest.TestCase):
    def test_no_telegram_migration_or_runtime_module_remains(self):
        versions=ROOT/"migrations/versions"
        self.assertFalse((versions/"0035_telegram_bridge.py").exists())
        self.assertFalse(any("telegram" in p.name.casefold() for p in ROOT.rglob("*.py") if p.name != Path(__file__).name))

    def test_obsolete_database_marker_is_repaired_before_upgrade(self):
        source=(ROOT/"common/db_migrate.py").read_text("utf-8")
        ast.parse(source, filename="common/db_migrate.py")
        helper=source.split("def _remove_legacy_telegram_revision",1)[1].split("def ensure_schema",1)[0]
        self.assertIn('"vk_revision": "0034_temporary_driver_until"', helper)
        self.assertIn('"obsolete_revision": "0035_telegram_bridge"', helper)
        call=source.index("_remove_legacy_telegram_revision()", source.index("def ensure_schema"))
        upgrade=source.index('command.upgrade(cfg, "head")', call)
        self.assertLess(call, upgrade)

    def test_vk_migration_graph_is_linear_and_has_one_head(self):
        revisions={}
        parents=set()
        for path in (ROOT/"migrations/versions").glob("*.py"):
            tree=ast.parse(path.read_text("utf-8"), filename=str(path))
            vals={}
            for node in tree.body:
                if isinstance(node,ast.Assign):
                    for target in node.targets:
                        if isinstance(target,ast.Name) and target.id in {"revision","down_revision"}:
                            vals[target.id]=ast.literal_eval(node.value)
            if vals.get("revision"):
                revisions[vals["revision"]]=path.name
                if vals.get("down_revision"): parents.add(vals["down_revision"])
        self.assertTrue(parents.issubset(revisions))
        self.assertEqual({"0042_route_parallel_offer_state"}, set(revisions)-parents)

if __name__=="__main__": unittest.main()
