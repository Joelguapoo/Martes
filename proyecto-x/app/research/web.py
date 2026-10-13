import ipaddress
import re
import socket
from datetime import UTC, datetime
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.ai.base import ChatMessage, LocalModelProvider
from app.research.models import ResearchResult, Source

PRIVATE_NETS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


class UnsafeUrlError(ValueError):
    """Raised when a candidate research URL is not safe to fetch."""


def assert_safe_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise UnsafeUrlError("Solo se permiten URLs HTTPS para investigacion web.")
    if not parsed.hostname:
        raise UnsafeUrlError("URL sin host.")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"No se pudo resolver el host: {parsed.hostname}") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if any(ip in network for network in PRIVATE_NETS):
            raise UnsafeUrlError("Bloqueado acceso a red local o privada.")


class WebResearcher:
    def __init__(
        self,
        model: LocalModelProvider,
        max_pages: int = 4,
        timeout: float = 20.0,
        tavily_enabled: bool = False,
        tavily_api_key: str | None = None,
    ) -> None:
        self.model = model
        self.max_pages = max_pages
        self.timeout = timeout
        self.tavily_enabled = tavily_enabled
        self.tavily_api_key = tavily_api_key

    async def research(self, query: str) -> ResearchResult:
        if not query.strip():
            raise ValueError("La pregunta de investigacion no puede estar vacia.")
        sources = await self._search_tavily(query) if self.tavily_enabled else []
        if not sources:
            sources = await self._search_duckduckgo(query)
        if not sources:
            return ResearchResult(
                query=query,
                summary="No pude recuperar fuentes accesibles para esta consulta.",
                facts=[],
                limitations=[
                    "La busqueda no devolvio paginas accesibles o la red no esta disponible."
                ],
                sources=[],
            )
        prompt_sources = "\n\n".join(
            f"Fuente {idx + 1}: {source.title}\nURL: {source.url}\nPasaje: {source.snippet}"
            for idx, source in enumerate(sources)
        )
        status = await self.model.status()
        if status.available:
            summary = await self.model.complete(
                [
                    ChatMessage(
                        "system",
                        "Resume solo con las fuentes dadas. "
                        "Diferencia hechos, inferencias y limites.",
                    ),
                    ChatMessage("user", f"Pregunta: {query}\n\n{prompt_sources}"),
                ],
                timeout=60,
            )
        else:
            summary = (
                "Recupere fuentes, pero no hay modelo local disponible para resumirlas. "
                "Revisa los pasajes y URLs listados."
            )
        return ResearchResult(
            query=query,
            summary=summary,
            facts=[source.snippet for source in sources[:3]],
            limitations=[] if status.available else [status.message],
            sources=sources,
        )

    async def _search_duckduckgo(self, query: str) -> list[Source]:
        search_url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
        async with httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            headers={"User-Agent": "ProyectoX/0.1 local research"},
        ) as client:
            response = await client.get(search_url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            links = []
            for result in soup.select(".result__a"):
                href = result.get("href")
                normalized = normalize_result_url(href) if href else None
                if normalized:
                    links.append((result.get_text(" ", strip=True), normalized))
                if len(links) >= self.max_pages:
                    break

            sources: list[Source] = []
            for title, url in links:
                try:
                    assert_safe_public_url(url)
                    page = await client.get(url)
                    page.raise_for_status()
                    snippet = extract_snippet(page.text)
                except Exception:
                    continue
                if snippet:
                    parsed = urlparse(url)
                    sources.append(
                        Source(
                            title=title or parsed.netloc,
                            site=parsed.netloc,
                            url=url,
                            accessed_at=datetime.now(UTC),
                            snippet=snippet[:1200],
                        )
                    )
            return sources

    async def _search_tavily(self, query: str) -> list[Source]:
        if not self.tavily_api_key:
            return []
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                headers={
                    "Authorization": f"Bearer {self.tavily_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "query": query,
                    "search_depth": "basic",
                    "max_results": self.max_pages,
                    "include_answer": False,
                    "include_raw_content": False,
                    "include_images": False,
                },
            )
            response.raise_for_status()
            data = response.json()
        sources: list[Source] = []
        for item in data.get("results", [])[: self.max_pages]:
            url = item.get("url")
            if not url:
                continue
            try:
                assert_safe_public_url(url)
            except UnsafeUrlError:
                continue
            parsed = urlparse(url)
            sources.append(
                Source(
                    title=item.get("title") or parsed.netloc,
                    site=parsed.netloc,
                    url=url,
                    accessed_at=datetime.now(UTC),
                    snippet=(item.get("content") or "")[:1200],
                )
            )
        return sources


def extract_snippet(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.extract()
    candidates = [p.get_text(" ", strip=True) for p in soup.find_all(["p", "article", "main"])]
    text = " ".join(part for part in candidates if len(part) > 80)
    return " ".join(text.split())[:1800]


def normalize_result_url(href: str) -> str | None:
    absolute = urljoin("https://duckduckgo.com", href)
    parsed = urlparse(absolute)
    query = parse_qs(parsed.query)
    if "uddg" in query:
        return unquote(query["uddg"][0])
    if parsed.scheme == "https" and not re.search(r"(^|\.)duckduckgo\.com$", parsed.netloc):
        return absolute
    return None
