from __future__ import annotations

import argparse
import json
import time
from datetime import date
from pathlib import Path

from langgraph.types import Command

from core.runtime import build_app, make_config
from core.state import initial_state
from core.viz import mermaid as graph_mermaid
from nodes.aggregate_hot_titles import aggregate_hot_titles_node
from nodes.fetch_douyin_hot import fetch_douyin_hot_node
from nodes.fetch_weibo_hot import fetch_weibo_hot_node
from nodes.llm_filter_articles import llm_filter_articles_node
from nodes.pick_hotspot_llm import pick_hotspot_llm_node
from nodes.prepare_selected_topic_materials import prepare_selected_topic_materials_node
from nodes.search_articles import search_articles_node, search_articles_selected_node
from nodes.search_videos import search_videos_node
from nodes.send_feishu_card_and_wait_selection import send_feishu_card_and_wait_selection_node
from nodes.aggregate_keyword_materials import aggregate_keyword_materials_node
from nodes.summarize_keyword_materials import summarize_keyword_materials_node
from nodes.fetch_article_contents_selected import fetch_article_contents_selected_node
from nodes.infer_account_topics_and_generate_proposals import (
    infer_account_topics_and_generate_proposals_node,
)
from tools.utils import new_thread_id


def main() -> None:
    parser = argparse.ArgumentParser(prog="hot-collect")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="Run daily workflow")
    run_p.add_argument("--run-date", default=date.today().isoformat())
    run_p.add_argument("--thread-id", default="")
    run_p.add_argument("--hotspot", default="", help="直接指定热点关键词，跳过微博/抖音抓取和LLM选题")
    run_p.add_argument("--no-human", action="store_true")

    res_p = sub.add_parser("resume", help="Resume from human selection")
    res_p.add_argument("--thread-id", required=True)
    res_p.add_argument("--proposal-id", required=True)

    n_p = sub.add_parser("node", help="Run single node (debug)")
    n_p.add_argument(
        "name",
        choices=[
            "fetch_weibo_hot",
            "fetch_douyin_hot",
            "fetch_hot_sources",
            "pick_hotspot_llm",
            "search_articles",
            "search_articles_selected",
            "search_videos",
            "llm_filter_articles_selected",
            "aggregate_article_text",
            "infer_account_topics_and_generate_proposals",
            "prepare_selected_topic_materials",
            "fetch_article_contents_selected",
            "send_feishu_card_and_wait_selection",
            "send_feishu_final_script",
            "download_subtitles_bbdown_selected",
            "aggregate_keyword_materials_selected",
            "summarize_keyword_materials_selected",
            "final_script",
            "split_script_chunks",
            "storyboard_segments",
            "storyboard_visuals",
        ],
        help="Node name to run",
    )
    n_p.add_argument("--run-date", default=date.today().isoformat())
    n_p.add_argument("--thread-id", default="")
    n_p.add_argument("--no-human", action="store_true")
    n_p.add_argument("--keyword", default="", help="Override keyword for debug nodes (e.g. search/filter)")
    n_p.add_argument("--proposal-id", default="A", help="Proposal id for selected-topic nodes")
    n_p.add_argument("--proposal-title", default="", help="Proposal title for selected-topic nodes")
    n_p.add_argument("--proposal-thesis", default="", help="Proposal thesis for selected-topic nodes")
    n_p.add_argument("--proposal-outline", default="", help="JSON list[str] for selected-topic nodes")
    n_p.add_argument("--hot-keyword", default="", help="Hot keyword (optional) for selected-topic nodes")
    n_p.add_argument("--article-analysis", default="", help="Article analysis text (for final_script debug)")
    n_p.add_argument("--video-analysis", default="", help="Video analysis text (for final_script debug)")
    n_p.add_argument("--article-analysis-path", default="", help="Path to article analysis text file")
    n_p.add_argument("--video-analysis-path", default="", help="Path to video analysis text file")
    n_p.add_argument("--url", default="", help="Single URL (for fetch_article_contents_selected debug)")
    n_p.add_argument("--video-url", default="", help="Single video url for download debug")
    n_p.add_argument("--video-title", default="", help="Single video title for download debug")
    n_p.add_argument(
        "--video-candidates-json",
        default="",
        help="JSON list[video_candidate] each has url/title/keyword for download debug",
    )
    n_p.add_argument(
        "--video-assets-json",
        default="",
        help="JSON list[video_asset] each has url/title/keyword/out_dir/audio_path (for aggregate/transcribe debug)",
    )
    n_p.add_argument("--narration-text", default="", help="Narration text input (for split/storyboard debug)")
    n_p.add_argument("--narration-path", default="", help="Path to narration text file")
    n_p.add_argument(
        "--keyword-materials-json",
        default="",
        help="JSON dict for keyword materials (e.g. {\"kw1\":{\"text\":\"...\"},\"kw2\":...})",
    )
    n_p.add_argument(
        "--keyword-materials-text-path",
        default="",
        help="Path to keyword materials long text (for summarize node debug)",
    )
    n_p.add_argument(
        "--hot-platform",
        default="",
        help="Hot platform (optional) for send_feishu_card_and_wait_selection (weibo/douyin/...)",
    )
    n_p.add_argument(
        "--proposals-json",
        default="",
        help="JSON list[proposal] for send_feishu_card_and_wait_selection (each has proposal_id/title/thesis/outline)",
    )
    n_p.add_argument("--json", action="store_true", help="Print raw JSON result")

    g_p = sub.add_parser("graph", help="输出工作流可视化（Mermaid）")
    g_p.add_argument("--output", default="", help="输出到文件（.mmd/.md）")
    g_p.add_argument("--lang", default="zh", choices=["zh", "en"], help="图中节点显示语言")
    g_p.add_argument("--dir", default="TB", help="图方向：TB/LR（默认 TB）")
    g_p.add_argument("--curve", default="stepAfter", help="连线样式：stepAfter/stepBefore/step/linear 等")
    g_p.add_argument("--node-spacing", type=int, default=60, help="节点间距（默认 60）")
    g_p.add_argument("--rank-spacing", type=int, default=80, help="层级间距（默认 80）")

    args = parser.parse_args()

    if args.cmd == "run":
        compiled = build_app(use_sqlite=True)
        thread_id = args.thread_id or new_thread_id(run_date=args.run_date)
        hotspot = str(getattr(args, "hotspot", "") or "").strip()
        state = {
            **initial_state(thread_id=thread_id, run_date=args.run_date),
            "human_mode": (not args.no_human),
        }
        if hotspot:
            state["direct_hotspot_keyword"] = hotspot
        cfg = make_config(
            thread_id=thread_id, run_date=args.run_date, human_mode=(not args.no_human)
        )
        out = compiled.invoke(state, config=cfg)
        interrupts = out.get("__interrupt__") or []
        if interrupts:
            print(f"thread_id={thread_id} INTERRUPT={[i.value for i in interrupts]}")
        else:
            print(f"thread_id={thread_id} status={out.get('status')}")
        return

    if args.cmd == "resume":
        compiled = build_app(use_sqlite=True)
        cfg = make_config(thread_id=args.thread_id)
        out = compiled.invoke(Command(resume={"proposal_id": args.proposal_id}), config=cfg)
        interrupts = out.get("__interrupt__") or []
        if interrupts:
            print(f"thread_id={args.thread_id} INTERRUPT={[i.value for i in interrupts]}")
        else:
            print(f"thread_id={args.thread_id} status={out.get('status')}")
        return

    if args.cmd == "node":
        thread_id = args.thread_id or new_thread_id(prefix="node", run_date=args.run_date)
        state = {
            **initial_state(thread_id=thread_id, run_date=args.run_date),
            "human_mode": (not args.no_human),
        }
        cfg = make_config(
            thread_id=thread_id, run_date=args.run_date, human_mode=(not args.no_human)
        )

        t0 = time.time()
        if args.name == "fetch_weibo_hot":
            patch = fetch_weibo_hot_node(state, cfg)
        elif args.name == "fetch_douyin_hot":
            patch = fetch_douyin_hot_node(state, cfg)
        elif args.name == "fetch_hot_sources":
            p1 = fetch_weibo_hot_node(state, cfg)
            state = {**state, **p1}
            p2 = fetch_douyin_hot_node(state, cfg)
            patch = {**p1, **p2}
        elif args.name == "pick_hotspot_llm":
            p1 = fetch_weibo_hot_node(state, cfg)
            state = {**state, **p1}
            p2 = fetch_douyin_hot_node(state, cfg)
            state = {**state, **p2}
            p3 = aggregate_hot_titles_node(state, cfg)
            state = {**state, **p3}
            p4 = pick_hotspot_llm_node(state, cfg)
            patch = {**p1, **p2, **p3, **p4}
        elif args.name == "search_articles":
            keyword = str(args.keyword or "").strip()
            if not keyword:
                raise SystemExit("--keyword is required for node=search_articles")
            state = {**state, "filtered_keywords": [keyword]}
            patch = search_articles_node(state, cfg)
        elif args.name == "search_articles_selected":
            raw = str(args.keyword or "").strip()
            if not raw:
                raise SystemExit("--keyword is required for node=search_articles_selected (use 'q1||q2||q3' for multiple)")
            keywords = [s.strip() for s in raw.split("||") if s.strip()]
            if not keywords:
                raise SystemExit("--keyword is required for node=search_articles_selected")

            proposal_id = str(args.proposal_id or "").strip() or "A"
            title = str(args.proposal_title or "").strip() or "debug_selected_title"
            thesis = str(args.proposal_thesis or "").strip()
            state = {
                **state,
                "selected_proposal_id": proposal_id,
                "proposals": [{"proposal_id": proposal_id, "title": title, "thesis": thesis, "outline": []}],
                "selected_hot_keyword": str(args.hot_keyword or "").strip(),
                "filtered_keywords": keywords,
            }
            patch = search_articles_selected_node(state, cfg)
        elif args.name == "search_videos":
            keyword = str(args.keyword or "").strip()
            if not keyword:
                raise SystemExit("--keyword is required for node=search_videos")
            state = {**state, "filtered_keywords": [keyword]}
            patch = search_videos_node(state, cfg)
        elif args.name == "llm_filter_articles_selected":
            raw = str(args.keyword or "").strip()
            if not raw:
                raise SystemExit("--keyword is required for node=llm_filter_articles_selected (use 'q1||q2||q3' for multiple)")
            keywords = [s.strip() for s in raw.split("||") if s.strip()]
            if not keywords:
                raise SystemExit("--keyword is required for node=llm_filter_articles_selected")

            proposal_id = str(args.proposal_id or "").strip() or "A"
            title = str(args.proposal_title or "").strip() or "debug_selected_title"
            thesis = str(args.proposal_thesis or "").strip()
            state = {
                **state,
                "selected_proposal_id": proposal_id,
                "proposals": [{"proposal_id": proposal_id, "title": title, "thesis": thesis, "outline": []}],
                "selected_hot_keyword": str(args.hot_keyword or "").strip(),
                "filtered_keywords": keywords,
            }
            p1 = search_articles_selected_node(state, cfg)
            state = {**state, **p1}
            patch = llm_filter_articles_node(state, cfg)
        elif args.name == "aggregate_article_text":
            from nodes.aggregate_article_text import aggregate_article_text_node

            keyword = str(args.keyword or "").strip()
            if not keyword:
                raise SystemExit("--keyword is required for node=aggregate_article_text")
            state = {**state, "filtered_keywords": [keyword]}
            p1 = search_articles_node(state, cfg)
            state = {**state, **p1}
            patch = aggregate_article_text_node(state, cfg)
        elif args.name == "infer_account_topics_and_generate_proposals":
            # 依赖：filtered_keywords + article_analysis（由上游文章链路产生）
            keyword = str(args.keyword or "").strip()
            if not keyword:
                raise SystemExit("--keyword is required for node=infer_account_topics_and_generate_proposals")
            # Debug shortcut: allow injecting article_analysis directly to avoid being blocked by some sites.
            injected_article = ""
            if str(args.article_analysis_path or "").strip():
                try:
                    injected_article = open(str(args.article_analysis_path), "r", encoding="utf-8").read()
                except Exception as e:
                    raise SystemExit(f"--article-analysis-path read failed: {e!r}") from e
            if str(args.article_analysis or "").strip():
                injected_article = str(args.article_analysis)

            if injected_article.strip():
                state = {
                    **state,
                    "filtered_keywords": [keyword],
                    "hot_titles": state.get("hot_titles") or [keyword],
                    "article_analysis": injected_article,
                }
                patch = infer_account_topics_and_generate_proposals_node(state, cfg)
            else:
                state = {**state, "filtered_keywords": [keyword]}
                p1 = search_articles_node(state, cfg)
                state = {**state, **p1}
                from nodes.aggregate_article_text import aggregate_article_text_node

                p3 = aggregate_article_text_node(state, cfg)
                state = {**state, **p3}
                patch = infer_account_topics_and_generate_proposals_node(state, cfg)
        elif args.name == "prepare_selected_topic_materials":
            title = str(args.proposal_title or "").strip()
            thesis = str(args.proposal_thesis or "").strip()
            if not title:
                raise SystemExit("--proposal-title is required for node=prepare_selected_topic_materials")
            outline: list[str] = []
            if str(args.proposal_outline or "").strip():
                try:
                    outline_raw = json.loads(str(args.proposal_outline))
                except Exception as e:
                    raise SystemExit(f"--proposal-outline must be valid JSON list[str]: {e!r}") from e
                if not isinstance(outline_raw, list):
                    raise SystemExit("--proposal-outline must be JSON list[str]")
                outline = [str(x).strip() for x in outline_raw if str(x).strip()]

            proposal_id = str(args.proposal_id or "").strip() or "A"
            proposal = {
                "proposal_id": proposal_id,
                "title": title,
                "thesis": thesis,
                "outline": outline,
            }
            state = {
                **state,
                "selected_proposal_id": proposal_id,
                "proposals": [proposal],
                "selected_hot_keyword": str(args.hot_keyword or "").strip(),
            }
            patch = prepare_selected_topic_materials_node(state, cfg)
        elif args.name == "fetch_article_contents_selected":
            url = str(getattr(args, "url", "") or "").strip()
            if not url:
                raise SystemExit("--url is required for node=fetch_article_contents_selected")
            keyword = str(args.keyword or "").strip() or "debug"
            state = {
                **state,
                "selected_proposal_id": str(args.proposal_id or "").strip() or "A",
                "filtered_keywords": [keyword],
                "article_candidates": [
                    {"keyword": keyword, "rank": 1, "title": "debug_article", "url": url, "selected": True}
                ],
            }
            patch = fetch_article_contents_selected_node(state, cfg)
        elif args.name == "aggregate_keyword_materials_selected":
            # 依赖上游：article_extracts 或 article_candidates + video_subtitles/video_transcripts
            # 这里支持传入 --keyword 作为 filtered_keywords（用 q1||q2||q3）
            raw = str(args.keyword or "").strip()
            if not raw:
                raise SystemExit("--keyword is required for node=aggregate_keyword_materials_selected (use 'q1||q2||q3')")
            keywords = [s.strip() for s in raw.split("||") if s.strip()]
            if not keywords:
                raise SystemExit("--keyword is required for node=aggregate_keyword_materials_selected")
            state = {**state, "selected_proposal_id": str(args.proposal_id or "").strip() or "A", "filtered_keywords": keywords}
            patch = aggregate_keyword_materials_node(state, cfg)
        elif args.name == "summarize_keyword_materials_selected":
            raw = str(args.keyword or "").strip()
            if not raw:
                raise SystemExit("--keyword is required for node=summarize_keyword_materials_selected (use 'q1||q2||q3')")
            keywords = [s.strip() for s in raw.split("||") if s.strip()]
            if not keywords:
                raise SystemExit("--keyword is required for node=summarize_keyword_materials_selected")
            by_kw_raw = str(args.keyword_materials_json or "").strip()
            text = ""
            if str(args.keyword_materials_text_path or "").strip():
                with open(str(args.keyword_materials_text_path), "r", encoding="utf-8") as f:
                    text = f.read().strip()
            if not by_kw_raw and not text:
                raise SystemExit(
                    "--keyword-materials-json or --keyword-materials-text-path is required for node=summarize_keyword_materials_selected"
                )
            by_kw: dict = {}
            if by_kw_raw:
                try:
                    parsed = json.loads(by_kw_raw)
                except Exception as e:
                    raise SystemExit(f"--keyword-materials-json must be valid JSON dict: {e!r}") from e
                if not isinstance(parsed, dict):
                    raise SystemExit("--keyword-materials-json must be JSON dict")
                by_kw = parsed

            state = {
                **state,
                "selected_proposal_id": str(args.proposal_id or "").strip() or "A",
                "filtered_keywords": keywords,
                "keyword_materials_by_keyword": by_kw,
                "keyword_materials_text": text,
            }
            patch = summarize_keyword_materials_node(state, cfg)
        elif args.name == "send_feishu_card_and_wait_selection":
            proposals: list[dict] = []
            proposals_raw = str(args.proposals_json or "").strip()
            if proposals_raw:
                try:
                    parsed = json.loads(proposals_raw)
                except Exception as e:
                    raise SystemExit(f"--proposals-json must be valid JSON list[proposal]: {e!r}") from e
                if not isinstance(parsed, list) or not parsed:
                    raise SystemExit("--proposals-json must be a non-empty JSON list[proposal]")
                proposals = [dict(x) for x in parsed if isinstance(x, dict)]
                if not proposals:
                    raise SystemExit("--proposals-json must contain at least one object proposal")
            else:
                title = str(args.proposal_title or "").strip()
                thesis = str(args.proposal_thesis or "").strip()
                if not title:
                    raise SystemExit(
                        "--proposal-title is required for node=send_feishu_card_and_wait_selection (or provide --proposals-json)"
                    )
                proposal_id = str(args.proposal_id or "").strip() or "A"
                proposals = [
                    {
                        "proposal_id": proposal_id,
                        "title": title,
                        "thesis": thesis,
                        "outline": [],
                    }
                ]

            state = {
                **state,
                "proposals": proposals,
                "selected_hot_keyword": str(args.hot_keyword or "").strip(),
                "hot_picked": {"platform": str(args.hot_platform or "").strip()},
            }
            patch = send_feishu_card_and_wait_selection_node(state, cfg)
        elif args.name == "send_feishu_final_script":
            from nodes.send_feishu_final_script import send_feishu_final_script_node

            script_md = str(args.narration_text or "").strip()
            if not script_md and str(args.narration_path or "").strip():
                with open(str(args.narration_path), "r", encoding="utf-8") as f:
                    script_md = f.read().strip()
            if not script_md:
                raise SystemExit(
                    "--narration-text (or --narration-path) is required for node=send_feishu_final_script"
                )
            state = {**state, "final_script_markdown": script_md, "status": "completed"}
            patch = send_feishu_final_script_node(state, cfg)
        elif args.name == "download_subtitles_bbdown_selected":
            from nodes.download_subtitles_bbdown import download_subtitles_bbdown_node

            candidates: list[dict] = []
            raw = str(args.video_candidates_json or "").strip()
            if raw:
                try:
                    parsed = json.loads(raw)
                except Exception as e:
                    raise SystemExit(
                        f"--video-candidates-json must be valid JSON list[video_candidate]: {e!r}"
                    ) from e
                if not isinstance(parsed, list) or not parsed:
                    raise SystemExit("--video-candidates-json must be a non-empty JSON list[video_candidate]")
                for item in parsed:
                    if not isinstance(item, dict):
                        continue
                    url = str(item.get("url") or "").strip()
                    if not url:
                        continue
                    candidates.append(
                        {
                            "url": url,
                            "title": str(item.get("title") or "").strip(),
                            "keyword": str(item.get("keyword") or "").strip(),
                        }
                    )
            else:
                url = str(args.video_url or "").strip()
                if not url:
                    raise SystemExit(
                        "--video-url is required for node=download_subtitles_bbdown_selected (or provide --video-candidates-json)"
                    )
                candidates = [
                    {
                        "url": url,
                        "title": str(args.video_title or "").strip() or "debug_video",
                        "keyword": str(args.keyword or "").strip() or "debug",
                    }
                ]

            state = {**state, "video_candidates": candidates}
            patch = download_subtitles_bbdown_node(state, cfg)
        elif args.name == "final_script":
            from nodes.final_script import final_script_node

            title = str(args.proposal_title or "").strip()
            thesis = str(args.proposal_thesis or "").strip()
            if not title:
                raise SystemExit("--proposal-title is required for node=final_script")

            article_analysis = str(args.article_analysis or "").strip()
            if str(args.article_analysis_path or "").strip():
                article_analysis = Path(str(args.article_analysis_path)).read_text(
                    encoding="utf-8", errors="ignore"
                ).strip()

            video_analysis = str(args.video_analysis or "").strip()
            if str(args.video_analysis_path or "").strip():
                video_analysis = Path(str(args.video_analysis_path)).read_text(
                    encoding="utf-8", errors="ignore"
                ).strip()

            proposal_id = str(args.proposal_id or "").strip() or "A"
            proposal = {
                "proposal_id": proposal_id,
                "title": title,
                "thesis": thesis,
                "outline": [],
            }
            state = {
                **state,
                "selected_proposal_id": proposal_id,
                "proposals": [proposal],
                "article_analysis": article_analysis,
                "video_analysis": video_analysis,
                "materials": [],
                "article_extracts": [],
                "video_transcripts": [],
            }
            patch = final_script_node(state, cfg)
        elif args.name == "split_script_chunks":
            from nodes.split_script_chunks import split_script_chunks_node

            narration = str(args.narration_text or "").strip()
            if str(args.narration_path or "").strip():
                narration = Path(str(args.narration_path)).read_text(encoding="utf-8", errors="ignore").strip()
            if not narration:
                raise SystemExit("--narration-text (or --narration-path) is required for node=split_script_chunks")
            state = {**state, "final_script_markdown": narration}
            patch = split_script_chunks_node(state, cfg)
        elif args.name == "storyboard_segments":
            from nodes.storyboard_segments import storyboard_segments_node

            narration = str(args.narration_text or "").strip()
            if str(args.narration_path or "").strip():
                narration = Path(str(args.narration_path)).read_text(encoding="utf-8", errors="ignore").strip()
            if not narration:
                raise SystemExit("--narration-text (or --narration-path) is required for node=storyboard_segments")

            title = str(args.proposal_title or "").strip() or "未命名视频"
            proposal_id = str(args.proposal_id or "").strip() or "A"
            state = {
                **state,
                "selected_proposal_id": proposal_id,
                "proposals": [{"proposal_id": proposal_id, "title": title, "thesis": "", "outline": []}],
                "final_script_markdown": narration,
            }
            patch = storyboard_segments_node(state, cfg)
        elif args.name == "storyboard_visuals":
            from nodes.storyboard_segments import storyboard_segments_node
            from nodes.storyboard_visuals import storyboard_visuals_node

            narration = str(args.narration_text or "").strip()
            if str(args.narration_path or "").strip():
                narration = Path(str(args.narration_path)).read_text(encoding="utf-8", errors="ignore").strip()
            if not narration:
                raise SystemExit("--narration-text (or --narration-path) is required for node=storyboard_visuals")

            title = str(args.proposal_title or "").strip() or "未命名视频"
            proposal_id = str(args.proposal_id or "").strip() or "A"
            state = {
                **state,
                "selected_proposal_id": proposal_id,
                "proposals": [{"proposal_id": proposal_id, "title": title, "thesis": "", "outline": []}],
                "final_script_markdown": narration,
            }
            p2 = storyboard_segments_node(state, cfg)
            state = {**state, **p2}
            patch = storyboard_visuals_node(state, cfg)
        else:
            raise SystemExit(f"unknown node: {args.name}")

        elapsed = time.time() - t0
        if args.json:
            print(json.dumps(patch, ensure_ascii=False, indent=2))
        else:
            weibo_n = len(patch.get("weibo_hot_items") or [])
            douyin_n = len(patch.get("douyin_hot_items") or [])
            if args.name in ("search_articles",):
                n = len(patch.get("article_search_results") or [])
                print(f"thread_id={thread_id} node={args.name} articles={n} secs={elapsed:.2f}")
            elif args.name in ("search_articles_selected",):
                n = len(patch.get("article_search_results") or [])
                print(f"thread_id={thread_id} node={args.name} articles={n} secs={elapsed:.2f}")
            elif args.name in ("aggregate_article_text",):
                analysis_len = len((patch.get("article_analysis") or "").strip())
                extracts_n = len(patch.get("article_extracts") or [])
                print(
                    f"thread_id={thread_id} node={args.name} extracts={extracts_n} analysis_chars={analysis_len} secs={elapsed:.2f}"
                )
            elif args.name in ("send_feishu_card_and_wait_selection",):
                status = str(patch.get("status") or "")
                msg_id = str(patch.get("feishu_message_id") or "")
                errors = patch.get("errors") or []
                if errors:
                    print(
                        f"thread_id={thread_id} node={args.name} status={status} errors={len(errors)} secs={elapsed:.2f}"
                    )
                else:
                    print(
                        f"thread_id={thread_id} node={args.name} status={status} message_id={msg_id} secs={elapsed:.2f}"
                    )
            elif args.name in ("download_subtitles_bbdown_selected",):
                ok = len(patch.get("video_assets") or [])
                failed = len(patch.get("video_asset_failures") or [])
                errs = len(patch.get("errors") or [])
                print(
                    f"thread_id={thread_id} node={args.name} ok={ok} failed={failed} errors={errs} secs={elapsed:.2f}"
                )
            elif args.name in ("final_script",):
                n = len((patch.get("final_script_markdown") or "").strip())
                print(f"thread_id={thread_id} node={args.name} script_chars={n} secs={elapsed:.2f}")
            elif args.name in ("split_script_chunks",):
                n = len(patch.get("script_chunks") or [])
                print(f"thread_id={thread_id} node={args.name} chunks={n} secs={elapsed:.2f}")
            elif args.name in ("storyboard_segments",):
                n = len(patch.get("storyboard_segments") or [])
                print(f"thread_id={thread_id} node={args.name} segments={n} secs={elapsed:.2f}")
            elif args.name in ("storyboard_visuals",):
                segs = (patch.get("storyboard") or {}).get("segments") or []
                print(f"thread_id={thread_id} node={args.name} segments={len(segs)} secs={elapsed:.2f}")
            else:
                print(
                    f"thread_id={thread_id} node={args.name} weibo={weibo_n} douyin={douyin_n} secs={elapsed:.2f}"
                )
        return

    if args.cmd == "graph":
        text = graph_mermaid(
            lang=args.lang,
            direction=args.dir,
            curve=args.curve,
            node_spacing=args.node_spacing,
            rank_spacing=args.rank_spacing,
        )
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(text)
            print(args.output)
        else:
            print(text)
        return


if __name__ == "__main__":
    main()
