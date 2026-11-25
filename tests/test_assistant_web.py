import unittest
from unittest.mock import patch
from xml.etree import ElementTree

import requests

from shared.assistant_web import WebSearchUnavailable, search_public_web, summarize_public_web


class AssistantWebTest(unittest.TestCase):
    def test_rss_preserves_publisher_date_and_safe_unique_links(self):
        items = ElementTree.fromstring('''
            <channel>
              <item><title>螺蛳粉产业</title><link>https://example.com/one</link>
                <source>产业周刊</source><pubDate>Tue, 24 Nov 2026 19:00:00 GMT</pubDate>
                <description>&lt;b&gt;公开摘要&lt;/b&gt;</description></item>
              <item><title>重复</title><link>https://example.com/one</link></item>
              <item><title>无效</title><link>javascript:alert(1)</link></item>
            </channel>
        ''').findall('item')
        with patch('shared.assistant_web._read_rss', return_value=items):
            sources = search_public_web('螺蛳粉 产业')
        self.assertEqual(sources, [{
            'title': '螺蛳粉产业', 'url': 'https://example.com/one', 'publisher': '产业周刊',
            'published_date': '2026-11-25', 'snippet': '公开摘要',
        }])

    def test_no_results_differs_from_failure_and_unknown_citations_are_invalid(self):
        with patch('shared.assistant_web._read_rss', side_effect=requests.Timeout):
            with self.assertRaises(WebSearchUnavailable):
                search_public_web('螺蛳粉')
        with patch('shared.assistant_web._read_rss', return_value=[]):
            self.assertEqual(search_public_web('螺蛳粉'), [])
        with patch('shared.assistant_web.request_json', return_value={'answer': '引用不存在。[2]'}):
            with self.assertRaises(ValueError):
                summarize_public_web('螺蛳粉行业如何', [{'title': '报道', 'url': 'https://example.com/one'}])
