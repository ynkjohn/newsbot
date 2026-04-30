from collector.rss_fetcher import should_keep_feed_entry


def test_metropoles_politics_filter_rejects_off_topic_sections():
    assert not should_keep_feed_entry(
        {
            "source_name": "Metropoles",
            "category": "politica-brasil",
            "url": "https://www.metropoles.com/entretenimento/cinema/o-diabo-veste-prada",
            "title": "Pré-estreia reúne fãs em Brasília",
        }
    )
    assert not should_keep_feed_entry(
        {
            "source_name": "Metropoles",
            "category": "politica-brasil",
            "url": "https://www.metropoles.com/saude/proteina-emagrecimento",
            "title": "Descubra qual proteína pode ajudar no emagrecimento",
        }
    )


def test_metropoles_politics_filter_keeps_political_sections():
    assert should_keep_feed_entry(
        {
            "source_name": "Metropoles",
            "category": "politica-brasil",
            "url": "https://www.metropoles.com/brasil/congresso-pl-dosimetria-bolsonaro",
            "title": "Câmara derruba veto de Lula à dosimetria",
        }
    )
