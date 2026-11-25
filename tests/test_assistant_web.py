import unittest
from unittest.mock import patch
from xml.etree import ElementTree

import requests

from shared.assistant_web import WebSearchUnavailable, search_public_web, search_regional_report, summarize_public_web


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
        with patch('shared.assistant_web._read_rss', return_value=items), patch(
            'shared.assistant_web._baidu_sources', return_value=None,
        ):
            sources = search_public_web('螺蛳粉 产业')
        self.assertEqual(sources, [{
            'title': '螺蛳粉产业', 'url': 'https://example.com/one', 'publisher': '产业周刊',
            'published_date': '2026-11-25', 'snippet': '公开摘要',
        }])

    def test_no_results_differs_from_failure_and_unknown_citations_are_invalid(self):
        with patch('shared.assistant_web._read_rss', side_effect=requests.Timeout), patch(
            'shared.assistant_web._baidu_sources', return_value=None,
        ):
            with self.assertRaises(WebSearchUnavailable):
                search_public_web('螺蛳粉')
        with patch('shared.assistant_web._read_rss', return_value=[]), patch(
            'shared.assistant_web._baidu_sources', return_value=None,
        ):
            self.assertEqual(search_public_web('螺蛳粉'), [])
        with patch('shared.assistant_web.request_json', return_value={'answer': '引用不存在。[2]'}):
            with self.assertRaises(ValueError):
                summarize_public_web('螺蛳粉行业如何', [{'title': '报道', 'url': 'https://example.com/one'}])

    def test_domestic_search_can_return_headlines_when_rss_is_unreachable(self):
        markup = b'''<h3><a href="https://www.baidu.com/link?url=article">Industry <em>headline</em></a></h3>
                     <h3><a href="javascript:alert(1)">Unsafe</a></h3>'''
        with patch('shared.assistant_web._read_rss', side_effect=requests.Timeout), patch(
            'shared.assistant_web._read_response', return_value=markup,
        ):
            sources = search_public_web('螺蛳粉 产业')
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]['title'], 'Industry headline')
        self.assertEqual(sources[0]['url'], 'https://www.baidu.com/link?url=article')
        self.assertIsNone(sources[0]['published_date'])

    def test_region_search_filters_unrelated_locations_and_deduplicates_across_queries(self):
        items = ElementTree.fromstring('''
            <channel>
              <item><title>柳州螺蛳粉产值</title><link>https://example.com/liuzhou</link></item>
              <item><title>深圳餐饮报道</title><link>https://example.com/other</link></item>
              <item><title>深圳餐饮</title><link>https://example.com/local</link>
                <description>深圳出现螺螄粉新门店</description></item>
            </channel>
        ''').findall('item')
        with patch('shared.assistant_web._read_rss', return_value=items), patch(
            'shared.assistant_web._baidu_sources', return_value=None,
        ):
            sources = search_regional_report('深圳', '螺蛳粉', retry=True)
        self.assertEqual([item['url'] for item in sources], ['https://example.com/local'])
