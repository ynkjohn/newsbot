import pytest

from collector.article_extractor import extract_article_content
from collector.article_extractor import _clean_article_text


@pytest.mark.asyncio
async def test_extracts_article_body_from_json_ld_when_visible_html_is_paywall(httpx_mock):
    html = """
    <html>
      <head>
        <script type="application/ld+json">
        {
          "@type": "NewsArticle",
          "headline": "Markets react to central bank decision",
          "articleBody": "Opening paragraph with the main news context. Second paragraph adds figures and reactions. Third paragraph explains the policy impact. Final paragraph says the decision will shape investor expectations through the next quarter."
        }
        </script>
      </head>
      <body>
        <article>
          <p>Subscribe to continue reading this article and receive our market coverage throughout the day.</p>
          <p>This preview is intentionally short and should not be stored as the full story.</p>
        </article>
      </body>
    </html>
    """
    httpx_mock.add_response(url="https://www.bloomberg.com/news/articles/example", text=html)

    content = await extract_article_content(
        "https://www.bloomberg.com/news/articles/example",
        fallback_description="RSS summary should not win when articleBody exists.",
    )

    assert "Opening paragraph with the main news context." in content
    assert "Final paragraph says the decision will shape investor expectations through the next quarter." in content
    assert "Subscribe to continue reading" not in content
    assert len(content) > 220


@pytest.mark.asyncio
async def test_removes_related_story_noise_from_article_paragraphs(httpx_mock):
    html = """
    <html>
      <body>
        <article>
          <p>BRASILIA - The government announced a new fiscal package after a meeting with congressional leaders.</p>
          <p>Leia também: veja como votou cada partido na sessão anterior.</p>
          <p>The proposal changes spending rules and creates a transition period for ministries.</p>
          <p>Analysts said the final text still depends on negotiations with state governors.</p>
          <p>At the end of the article, lawmakers said the vote should happen after the budget report is published.</p>
        </article>
      </body>
    </html>
    """
    httpx_mock.add_response(url="https://www.metropoles.com/brasil/example", text=html)

    content = await extract_article_content("https://www.metropoles.com/brasil/example")

    assert "The government announced a new fiscal package" in content
    assert "At the end of the article, lawmakers said the vote should happen" in content
    assert "Leia também" not in content
    assert len(content.splitlines()) >= 4


@pytest.mark.asyncio
async def test_rejects_generic_page_chrome_and_falls_back_to_rss_summary(httpx_mock):
    html = """
    <html>
      <body>
        <nav><p>Home Politics Economy Markets World Subscribe Newsletter Login Search Menu</p></nav>
        <main>
          <p>Enable JavaScript and disable your ad blocker to keep reading this page with all available features.</p>
          <p>Sign in to continue. This page preview does not contain the article body requested by the feed.</p>
        </main>
      </body>
    </html>
    """
    httpx_mock.add_response(url="https://g1.globo.com/politica/noticia/example?outputType=amp", text=html)

    content = await extract_article_content(
        "https://g1.globo.com/politica/noticia/example",
        fallback_description="<p>RSS summary with the actual story context and enough detail to be useful.</p>",
    )

    assert content == "RSS summary with the actual story context and enough detail to be useful."
    assert "Enable JavaScript" not in content


def test_rejects_corrupted_non_text_candidates():
    corrupted = "\ufffd \ufffd2U{\ufffd\ufffdf\ufffd\ufffd R\ufffd1'\ufffd\ufffd+]\ufffd\ufffd\ufffdh"

    assert _clean_article_text(corrupted) == ""
