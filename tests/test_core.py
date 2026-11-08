import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/_zotero_shared"))
sys.path.insert(0, str(ROOT / "skills/_zotero_shared/scripts"))

from md2zotero import convert  # noqa: E402
import zotero_push as zp  # noqa: E402
from agent_ranker import apply_scores, prepare_request  # noqa: E402
from zdp_config import daily_papers_config  # noqa: E402


class MarkdownTests(unittest.TestCase):
    def test_math(self):
        out = convert("核心 $p(y|v)$ 与\n\n$$\n\\sum_i x_i\n$$")
        self.assertIn('<span class="math">$p(y|v)$</span>', out)
        self.assertIn('<pre class="math">$$\\sum_i x_i$$</pre>', out)

    def test_currency_not_math(self):
        self.assertNotIn("math", convert("costs $5 and $10 total"))

    def test_table_with_math_pipe(self):
        out = convert("| a | $\\|x\\|$ |\n|---|---|\n| 1 | 2 |")
        self.assertIn("<th>a</th>", out)
        self.assertEqual(out.count("<td>"), 2)

    def test_lists_split_by_type(self):
        out = convert("- a\n  - b\n1. c")
        self.assertIn("<ul><li>a<ul><li>b</li></ul></li></ul><ol><li>c</li></ol>", out)

    def test_escape_and_unsafe_link(self):
        out = convert("<script>x</script> [x](javascript:alert(1))")
        self.assertNotIn("<script>", out)
        self.assertIn('href="#"', out)

    def test_snake_case_not_italic(self):
        self.assertNotIn("<em>", convert("snake_case_word"))


class ConfigTests(unittest.TestCase):
    def test_directions_drive_keywords(self):
        cfg = daily_papers_config()
        ids = [d["id"] for d in cfg["directions"]]
        self.assertEqual(ids, ["mllm-hallucination", "post-training", "architecture"])
        self.assertIn("hallucination", cfg["keywords"])
        self.assertIn("grpo", cfg["keywords"])
        self.assertIn("mixture of experts", cfg["keywords"])
        self.assertEqual(len(cfg["research_interests"]), 3)

    def test_synonym_group_scores_once(self):
        import fetch_and_score as fs
        group = next(g for g in daily_papers_config()["keyword_groups"] if "hallucination" in g)
        self.assertIn("hallucinations", group)
        one = fs.score_paper({"title": "x", "abstract": "we study hallucination"})
        many = fs.score_paper({"title": "x", "abstract": "hallucination, hallucinations, object hallucination"})
        self.assertEqual(one, many)
        self.assertGreater(fs.score_paper({"title": "hallucination", "abstract": ""}), one)

    def test_directions_scored_separately(self):
        import fetch_and_score as fs
        one = {"title": "x", "abstract": "we reduce object hallucination"}
        cross = {"title": "x", "abstract": "we reduce object hallucination with grpo and a reward model"}
        self.assertEqual(fs.score_paper(one), 2)
        self.assertEqual(fs.score_paper(cross), 4)  # best direction (post-training: 2 + 2), no sum, no bonus
        self.assertEqual(cross["direction_scores"], {"mllm-hallucination": 2, "post-training": 4})

    def test_quality_multiplier(self):
        import fetch_and_score as fs
        venue, accepted = fs.QUALITY["venue_multiplier"], fs.QUALITY["accepted_multiplier"]
        self.assertEqual(fs.quality_multiplier("Accepted to NeurIPS 2026"), (venue, "NeurIPS"))
        self.assertEqual(fs.quality_multiplier("CVPR 2027 camera ready")[0], venue)
        self.assertEqual(fs.quality_multiplier("Accepted at the ICLR 2026 Workshop on X")[0], accepted)
        self.assertEqual(fs.quality_multiplier("Accepted by TMLR"), (accepted, "accepted"))
        self.assertEqual(fs.quality_multiplier("Accepted by ACM Multimedia 2026")[0], venue)
        self.assertEqual(fs.quality_multiplier("Accepted to Findings of ACL 2026")[0], venue)
        self.assertEqual(fs.quality_multiplier("Accepted to NAACL 2026")[0], accepted)  # ACL must not match NAACL
        self.assertEqual(fs.quality_multiplier("Under review at ICLR 2027")[0], 1.0)
        self.assertEqual(fs.quality_multiplier("12 pages, 5 figures")[0], 1.0)
        self.assertEqual(fs.quality_multiplier(None)[0], 1.0)

    def test_focus_bonus(self):
        import fetch_and_score as fs
        saved = fs.FOCUS_GROUPS
        try:
            fs.FOCUS_GROUPS = [["looped transformer"], ["speculative decoding", "draft model"]]
            # direction hit (abstract, 2) + focus bonus
            self.assertEqual(fs.score_paper({"title": "x", "abstract": "a looped transformer"}), 2 + fs.FOCUS_BONUS)
            # focus-only hit still qualifies; synonyms in one focus group count once
            self.assertEqual(fs.score_paper({"title": "x", "abstract": "speculative decoding with a draft model"}),
                             fs.FOCUS_BONUS)
        finally:
            fs.FOCUS_GROUPS = saved

    def test_arxiv_window_start(self):
        from datetime import datetime, timezone
        import fetch_and_score as fs
        utc = lambda *a: datetime(*a, tzinfo=timezone.utc)
        # Thu 2026-11-05 13:59 EST (18:59 UTC) -> batch opened Wed 14:00 EST (19:00 UTC)
        self.assertEqual(fs.arxiv_window_start(utc(2026, 11, 5, 18, 59), 1), utc(2026, 11, 4, 19, 0))
        # Monday's batch reaches back over the weekend to Friday 14:00
        self.assertEqual(fs.arxiv_window_start(utc(2026, 11, 9, 18, 30), 1), utc(2026, 11, 6, 19, 0))
        # two batches from Monday -> Thursday 14:00
        self.assertEqual(fs.arxiv_window_start(utc(2026, 11, 9, 18, 30), 2), utc(2026, 11, 5, 19, 0))
        # EDT (summer): cutoff is 18:00 UTC
        self.assertEqual(fs.arxiv_window_start(utc(2026, 7, 8, 17, 0), 1), utc(2026, 7, 7, 18, 0))

    def test_recall_min_score_config(self):
        cfg = daily_papers_config()
        self.assertIn("recall_min_score", cfg)
        self.assertNotIn("min_score", cfg)
        self.assertIn("min_score", cfg["ranking"])

    def test_keyword_entry_validation(self):
        from zdp_config import keyword_group
        self.assertEqual(keyword_group(["a", " b "], "t"), ["a", "b"])
        for bad in ([], [""], 3, ["a", 1]):
            with self.assertRaises(ValueError):
                keyword_group(bad, "t")


class RankerTests(unittest.TestCase):
    def test_roundtrip(self):
        cfg = daily_papers_config()
        papers = [{"url": f"https://arxiv.org/abs/2611.0000{i}", "title": f"t{i}", "score": i} for i in range(3)]
        req = prepare_request(papers, cfg, 2)
        resp = {"run_id": req["run_id"], "scores": [
            {"id": 0, "score": 4, "reason": "[post-training] x"},
            {"id": 1, "score": 1, "reason": "y"},
            {"id": 2, "score": 3, "reason": "[architecture] z"}]}
        selected, report = apply_scores(req, resp)
        self.assertEqual([p["title"] for p in selected], ["t0", "t2"])
        with self.assertRaises(ValueError):
            apply_scores(req, {"run_id": "stale", "scores": resp["scores"]})


class ZoteroPayloadTests(unittest.TestCase):
    def test_item_payload_prefers_author_list(self):
        meta = {"arxiv_id": "2311.16922", "title": "T", "authors": "John Smith, Jr., Ann Lee",
                "author_list": ["John Smith, Jr.", "Ann Lee"]}
        self.assertEqual(len(zp.item_payload(meta, [], [])["creators"]), 2)

    def test_item_payload(self):
        obj = zp.item_payload({"arxiv_id": "2311.16922", "title": "VCD", "authors": "Sicong Leng, Plato"},
                              ["C1"], ["dp", "dp"])
        self.assertEqual(obj["itemType"], "preprint")
        self.assertEqual(obj["DOI"], "10.48550/arXiv.2311.16922")
        self.assertEqual(obj["creators"][1], {"creatorType": "author", "name": "Plato"})
        self.assertEqual(obj["tags"], [{"tag": "dp"}])

    def test_item_matches(self):
        self.assertTrue(zp.item_matches({"itemType": "preprint", "url": "https://arxiv.org/abs/2311.16922v2"}, "2311.16922"))
        self.assertFalse(zp.item_matches({"itemType": "note", "url": "2311.16922"}, "2311.16922"))
        self.assertFalse(zp.item_matches({"itemType": "preprint", "url": "x", "deleted": 1}, "2311.16922"))

    def test_dry_run_push(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            (t / "e.json").write_text(json.dumps([{"arxiv_id": "2311.16922", "title": "VCD", "authors": "A B"}]))
            (t / "p.json").write_text(json.dumps({
                "date": "2026-11-04",
                "digest_markdown": "# d\n\n[VCD](zotero:2311.16922) [M](zotero:2312.00752)",
                "papers": [{"arxiv_id": "2311.16922", "tier": "must", "directions": ["mllm-hallucination"],
                            "review_markdown": "# r", "collection_keys": []},
                           {"arxiv_id": "2312.00752", "tier": "skip"}]}))
            env = {**os.environ, "ZOTERO_API_KEY": "", "ZDP_TEMP_DIR": tmp, "XDG_CONFIG_HOME": tmp}
            run = subprocess.run([sys.executable, str(ROOT / "skills/_zotero_shared/zotero_push.py"), "--dry-run",
                                  "push-daily", "--payload", str(t / "p.json"), "--enriched", str(t / "e.json")],
                                 env=env, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            result = json.loads((t / "zotero_push_result.json").read_text())
            self.assertTrue(result["papers"]["2311.16922"]["item_key"].startswith("DRY"))
            self.assertNotIn("item_key", result["papers"]["2312.00752"])
            preview = (t / "zotero_digest_preview.html").read_text()
            self.assertIn('href="zotero://select/library/items/DRY', preview)
            self.assertIn('href="https://arxiv.org/abs/2312.00752"', preview)  # skip tier -> arXiv link
            self.assertFalse((t / "zotero-daily-papers" / "history.json").exists())

    def test_history_path_setting(self):
        import zdp_config
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            def configured(value):
                (t / "c.json").write_text(json.dumps({"daily_papers": {"history_path": value}}))
                zdp_config.load_config.cache_clear()
                return zdp_config.history_path()
            env = {"ZDP_CONFIG": str(t / "c.json"), "XDG_CONFIG_HOME": str(t / "xdg"), "ZDP_SYNC": str(t / "sync")}
            try:
                with patch.dict(os.environ, env):
                    self.assertEqual(configured(""), t / "xdg" / "zotero-daily-papers" / "history.json")
                    self.assertEqual(configured("$ZDP_SYNC/zdp/history.json"), t / "sync" / "zdp" / "history.json")
                    zdp_config.save_history([{"id": "2311.16922", "date": "2026-11-04"}])
                    self.assertEqual(zdp_config.load_history()[0]["id"], "2311.16922")
                    self.assertTrue((t / "sync" / "zdp" / "history.json").is_file())
                    self.assertEqual(configured("~/h.json"), Path("~/h.json").expanduser())
                    with self.assertRaises(ValueError):
                        configured("relative/history.json")
            finally:
                zdp_config.load_config.cache_clear()


if __name__ == "__main__":
    unittest.main()
