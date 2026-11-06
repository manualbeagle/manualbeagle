"""The MCP server (stdio): `manualbeagle serve --mcp`.

Tools: find_manual, search_manuals, ask_manuals, read_pages, list_documents, maintenance_suggestions, and, with
--allow-write, fetch_manual (downloads into the local library). Needs the optional `mcp` package (`pip install manualbeagle[mcp]`, MCP SDK 2.x).
"""

from __future__ import annotations

from .adapters import Registry
from .config import Config
from .library import Library
from .models import Candidate, Query
from .pipeline import find
from .search import make_engine


def build(cfg: Config, allow_write: bool = False):
    """Build the MCP server and its tools. Tools that write files exist only with `allow_write`."""
    from mcp.server.mcpserver import MCPServer

    server = MCPServer(
        name="manualbeagle",
        instructions=(
            "Finds the maker's manual for an appliance from its brand and model, and searches downloaded manuals "
            "page by page. Cite the document and page when you quote a manual."
        ),
    )
    reg = Registry(cfg.adapter_dirs)

    @server.tool()
    def find_manual(brand: str, model: str, year: int | None = None, limit: int = 8) -> list[dict]:
        """Find manuals for a brand and model (add year for vehicles). Returns ranked candidates; saves nothing."""
        engine = make_engine(cfg.search.engine, url=cfg.search.url)
        with cfg.client() as http:
            res = find(Query(brand=brand, model=model, year=year), reg, http, engine)
        return [
            {
                "url": c.url,
                "title": c.title,
                "kind": c.kind.value if c.kind else None,
                "score": c.score,
                "link_only": c.link_only,
                "source": c.source,
                "why": c.reasons,
            }
            for c in res.candidates[:limit]
        ]

    @server.tool()
    def search_manuals(
        query: str, brand: str | None = None, model: str | None = None, limit: int = 10
    ) -> list[dict]:
        """Full-text search of downloaded manuals (SQLite FTS5 syntax). Each hit names its document and page."""
        lib = Library(cfg.library, cfg.db_path)
        try:
            return lib.search(query, brand=brand, model=model, limit=limit)
        finally:
            lib.close()

    @server.tool()
    def ask_manuals(
        question: str,
        brand: str | None = None,
        model: str | None = None,
        document: str | None = None,
        pages: int = 4,
    ) -> dict:
        """Answer a question from the downloaded manuals only, citing each page used as [n] (see `sources`).

        Narrow it with brand, model or document (a sha256). Uses the configured [ai] model; with none, `status`
        is "pages_only" and `sources` are the best pages to read with read_pages. "not_found" means the
        manuals don't say. Read-only.
        """
        from . import ask

        lib = Library(cfg.library, cfg.db_path)
        try:
            with ask.model_client(cfg) as http:
                try:
                    llm = ask.make_model(cfg.ai, http)
                except ask.AskError as e:
                    return {"error": str(e)}
                return ask.ask(lib, question, llm, brand, model, document, pages).to_dict()
        finally:
            lib.close()

    @server.tool()
    def read_pages(document: str, pages: list[int]) -> dict[int, str]:
        """Return the text of specific pages of a downloaded document (its sha256 from search or list_documents)."""
        lib = Library(cfg.library, cfg.db_path)
        try:
            return lib.read_pages(document, pages[:20])
        finally:
            lib.close()

    @server.tool()
    def list_documents() -> list[dict]:
        """Every document in the local library: brand, model, kind, pages and source URL."""
        lib = Library(cfg.library, cfg.db_path)
        try:
            return [{k: v for k, v in d.items() if k != "path"} for d in lib.documents()]
        finally:
            lib.close()

    @server.tool()
    def maintenance_suggestions(
        status: str = "new", brand: str | None = None, model: str | None = None
    ) -> list[dict]:
        """Maintenance tasks read from the manuals, such as "clean the filter every 2 weeks". Read-only.

        `status` is new (waiting for a person to approve), filed (approved and added to Homebox), dismissed, or
        all. Each has the task, how often (`every`), the item, and the document, page and quote it came from:
        cite them. A person approves suggestions in the web UI; this tool can't.
        """
        from .maintenance import STATUSES, Store

        if status not in (*STATUSES, "all"):
            raise ValueError("status must be new, filed, dismissed or all")
        lib = Library(cfg.library, cfg.db_path)
        try:
            rows = Store(lib).list(None if status == "all" else status)
        finally:
            lib.close()
        keep = (
            "id", "task", "every", "first_date", "item_name", "brand", "model", "sha256", "title", "page", "quote",
            "source", "status", "scheduled",
        )  # fmt: skip
        return [
            {k: s.public()[k] for k in keep}
            for s in rows
            if (not brand or s.brand.lower() == brand.lower())
            and (not model or s.model.lower() == model.lower())
        ]

    if allow_write:

        @server.tool()
        def fetch_manual(url: str, brand: str, model: str, title: str = "") -> dict:
            """Download one manual into the local library, index its pages, and check its text names the model.

            Refuses a URL that is only for a person to open (a manual mirror, or a brand whose site refuses
            automated requests): find_manual marks those link_only.
            """
            from .normalize import normalise

            refusal = reg.download_refusal(url)
            if refusal:
                return {"error": f"not downloaded: {refusal}", "url": url}
            lib = Library(cfg.library, cfg.db_path)
            try:
                # The same check on every redirect hop, so a URL can't bounce to a mirror.
                with cfg.client(refuse=reg.download_refusal) as http:
                    doc = lib.fetch(
                        Candidate(url=url, title=title, source="mcp"),
                        normalise(Query(brand=brand, model=model)),
                        http,
                    )
                return {
                    "document": doc.sha256,
                    "pages": doc.page_count,
                    "ocr": doc.ocr,
                    "check": doc.check_reasons,
                }
            finally:
                lib.close()

    return server


def run(cfg: Config, allow_write: bool = False) -> int:
    """Run the MCP server over stdio until the client disconnects."""
    build(cfg, allow_write).run("stdio")
    return 0
