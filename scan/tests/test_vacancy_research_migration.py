import unittest
from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "db_migrations"
    / "supa_migration_vacancy_research.sql"
)


class VacancyResearchMigrationTests(unittest.TestCase):
    def test_claim_rpc_supports_exact_apn_filter(self) -> None:
        migration = MIGRATION.read_text(encoding="utf-8")
        self.assertIn("p_apn           TEXT    DEFAULT NULL", migration)
        self.assertIn("b.apn = TRIM(p_apn)", migration)
        self.assertIn(
            "GRANT EXECUTE ON FUNCTION public.claim_vacancy_research(TEXT, INTEGER, TEXT, TEXT[], TEXT)",
            migration,
        )

    def test_claim_rpc_drops_prior_signatures_before_replacement(self) -> None:
        migration = MIGRATION.read_text(encoding="utf-8")
        self.assertIn("DROP FUNCTION IF EXISTS public.claim_vacancy_research(TEXT, INTEGER, TEXT);", migration)
        self.assertIn(
            "DROP FUNCTION IF EXISTS public.claim_vacancy_research(TEXT, INTEGER, TEXT, TEXT[]);",
            migration,
        )


if __name__ == "__main__":
    unittest.main()
