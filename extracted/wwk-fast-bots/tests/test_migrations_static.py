"""Статическая сверка моделей и миграций (без SQLAlchemy/PostgreSQL).
Полная проверка (alembic upgrade head на пустом PostgreSQL) выполняется в CI - см. .github/workflows/ci.yml."""
import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).parent.parent


def _models_tables():
    tree = ast.parse((ROOT / "wwk/db/models.py").read_text())
    out = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.ClassDef):
            for st in n.body:
                if isinstance(st, ast.Assign) and getattr(st.targets[0], "id", "") == "__tablename__":
                    cols = set()
                    for s2 in n.body:
                        if isinstance(s2, ast.AnnAssign) and isinstance(s2.target, ast.Name):
                            cols.add(s2.target.id)
                    out[st.value.value] = cols
    return out


def _migration_tables():
    tree = ast.parse((ROOT / "migrations/versions/0001_initial.py").read_text())
    out = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "create_table":
            name = n.args[0].value
            cols = {c.args[0].value for c in n.args[1:] if isinstance(c, ast.Call) and getattr(c.func, "attr", "") == "Column"}
            out[name] = cols
    return out


class StaticMigrationTests(unittest.TestCase):
    def test_tables_and_columns_match(self):
        self.assertEqual(_models_tables(), _migration_tables())

    def test_revision_chain(self):
        revs = {}
        for f in (ROOT / "migrations/versions").glob("*.py"):
            tree = ast.parse(f.read_text())
            vals = {t.targets[0].id: ast.literal_eval(t.value) for t in tree.body
                    if isinstance(t, ast.Assign) and isinstance(t.targets[0], ast.Name)
                    and t.targets[0].id in ("revision", "down_revision")}
            revs[vals["revision"]] = vals["down_revision"]
        heads = set(revs) - set(v for v in revs.values() if v)
        self.assertEqual(len(heads), 1, "должна быть ровно одна голова миграций")
        self.assertEqual(sum(1 for v in revs.values() if v is None), 1, "должна быть одна корневая миграция")


if __name__ == "__main__":
    unittest.main()
