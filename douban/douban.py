#!/usr/bin/env python
# vim:fileencoding=UTF-8:ts=4:sw=4:sta:et:sts=4:ai

from __future__ import absolute_import, division, print_function, unicode_literals

from calibre.rpdb import set_trace

__license__ = 'GPL v3'
__copyright__ = '2011, Kovid Goyal <kovid@kovidgoyal.net>; 2011, Li Fanxi <lifanxi@freemindworld.com>; 2021 Driftcrow'
__docformat__ = 'restructuredtext en'

import time
try:
    from queue import Empty, Queue
except ImportError:
    from Queue import Empty, Queue

import re
from calibre.ebooks.metadata import check_isbn
from calibre.ebooks.metadata.sources.base import Option, Source
from calibre.ebooks.metadata.book.base import Metadata
from calibre import as_unicode

def get_details(browser, url, timeout):  # {{{
    try:
        raw = browser.open_novisit(url, timeout=timeout).read()
    except Exception as e:
        gc = getattr(e, 'getcode', lambda: -1)
        if gc() != 403:
            raise
        # Douban is throttling us, wait a little
        time.sleep(2)
        raw = browser.open_novisit(url, timeout=timeout).read()

    return raw


# }}}


xpath_cache = {}


def XPath(x):
    ans = xpath_cache.get(x)
    if ans is None:
        from lxml import etree
        ans = xpath_cache[x] = etree.XPath(x)
    return ans

class Douban(Source):

    name = 'Douban Books'
    author = 'Li Fanxi, xcffl, jnozsc, Driftcrow'
    version = (3, 2, 1)
    minimum_calibre_version = (2, 80, 0)

    description = _(
        'Downloads metadata and covers from Douban.com. '
        'Useful only for Chinese language books.'
    )

    capabilities = frozenset(['identify', 'cover'])
    touched_fields = frozenset([
        'title', 'authors', 'tags', 'pubdate', 'comments', 'publisher',
        'identifier:isbn', 'rating', 'identifier:douban'
    ])  # language currently disabled
    supports_gzip_transfer_encoding = True
    cached_cover_url_is_reliable = True

    # DOUBAN_API_KEY = ''
    BASE_URL = 'https://www.douban.com/j/search?t=book&p=0&'
    # DOUBAN_API_URL = 'https://m.douban.com/j/search/?q=starwars&t=book&p=0'
    DOUBAN_BOOK_URL = 'https://book.douban.com/subject/%s/'

    options = (
        Option(
            'include_subtitle_in_title', 'bool', True,
            _('Include subtitle in book title:'),
            _('Whether to append subtitle in the book title.')
        ),
    )

    def to_metadata(self, browser, log, entry_, timeout):  # {{{
        from lxml import etree

        # total_results  = XPath('//openSearch:totalResults')
        # start_index    = XPath('//openSearch:startIndex')
        # items_per_page = XPath('//openSearch:itemsPerPage')
        # entry = XPath('//div[@class="article"]')
        entry = XPath('/*')
        # entry_id = XPath('descendant::atom:id')
        # url = XPath('descendant::atom:link[@rel="self"]/@href')
        authors = XPath('//meta[@property="book:author"]/@content')
        # identifier = XPath('descendant::identifier')
        # subject = XPath('descendant::div[@id="info"]/br[2]')
        description = XPath('string(//div[@class="intro"])')
        # language = XPath('descendant::language')
        cover =XPath('//meta[@property="og:image"]/@content')
        isbn = XPath('//meta[@property="book:isbn"]/@content')
        rating = XPath('//strong[@property="v:average"]')
        tags = XPath('string(//div[@id="db-tags-section"]//div[@class="indent"])')

        result = re.compile(r'<a class=.*? href="(.*?)" target=.*? title="(.*?)" >.*?sid:(.*?),', re.S).search(entry_)

        title = result.group(2)
        details_url = result.group(1)
        douban_id = result.group(3)

        if not title:
            # Silently discard this entry
            return None

        mi = Metadata(title)
        mi.identifiers = {'douban': douban_id}
        mi.language = 'Chinese'

        try:
            raw = get_details(browser, details_url, timeout)
            feed = etree.HTML(raw.decode('utf-8'))
            extra = entry(feed)[0]
        except:
            log.exception('Failed to get additional details for', mi.title)
            return mi

        info = etree.tostring(etree.XPath('//div[@id="info"]')(extra)[0], encoding='utf8').decode()
        # result = re.compile(r'出版社:</span>(.*?)<.*?副标题:</span>(.*?)<.*?出版年:</span>(.*?)<.*?丛书:</span>.*?>(.*?)</a>', re.S).search(info)
        result = re.compile(r'出版社:</span>(.*?<a.*?>)*(.*?)<', re.M).search(info)
        if result:
            mi.publisher = result.group(2).strip()

        result = re.compile(r'副标题:</span>(.*?)<', re.M).search(info)
        if result:
            mi.subtitle = result.group(1).strip()

        authors =  authors(extra)
        if not authors:
            authors = [('Unknown')]
        else:
            authors = authors[0].split('、')    # TODO:: others split char

        mi.authors = authors

        cover_url = cover(extra)[0]

        mi.comments = description(extra)



        # ISBN
        isbns = []
        isbn =  isbn(extra)[0]
        if isinstance(isbn, (type(''), bytes)):
            if check_isbn(isbn):
                isbns.append(isbn)
        else:
            for x in isbn:
                if check_isbn(x):
                    isbns.append(x)
        if isbns:
            mi.isbn = sorted(isbns, key=len)[-1]
        mi.all_isbns = isbns

        # Tags
        tags = [ tag.strip() for tag in tags(extra).split("\n") if tag.strip() != '']
        # tags = tags(extra)[0].split(" ")
        mi.tags = tags

        # pubdate
        pubdate = ''
        result = re.compile(r'出版年:</span>(.*?)<', re.M).search(info)
        if result:
            pubdate = result.group(1)

        if pubdate:
            from calibre.utils.date import parse_date, utcnow
            try:
                default = utcnow().replace(day=15)
                mi.pubdate = parse_date(pubdate, assume_utc=True, default=default)
            except:
                log.error('Failed to parse pubdate %r' % pubdate)

        # Ratings
        rating = rating(extra)[0].text
        if rating:
            try:
                mi.rating = float(rating) / 2.0
            except:
                log.exception('Failed to parse rating')
                mi.rating = 0

        # Cover
        mi.has_douban_cover = None
        u = cover_url
        if u:
            # If URL contains "book-default", the book doesn't have a cover
            if u.find('book-default') == -1:
                mi.has_douban_cover = u

        # Series
        result = re.compile(r'丛书:</span>(.*?<a.*?>)*(.*?)<', re.M).search(info)
        if result:
            mi.series = result.group(2)

        return mi

    # }}}

    def get_book_url(self, identifiers):  # {{{
        db = identifiers.get('douban', None)
        if db is not None:
            return ('douban', db, self.DOUBAN_BOOK_URL % db)

    # }}}

    def create_query(self, log, title=None, authors=None, identifiers={}):  # {{{
        try:
            from urllib.parse import urlencode
        except ImportError:
            from urllib import urlencode

        q = ''
        t = None
        isbn = check_isbn(identifiers.get('isbn', None))
        subject = identifiers.get('douban', None)
        if isbn is not None:
            q = isbn
        elif subject is not None:
            q = subject
        elif title or authors:

            def build_term(prefix, parts):
                return ' '.join(x for x in parts)

            title_tokens = list(self.get_title_tokens(title))
            if title_tokens:
                q += build_term('title', title_tokens)
            author_tokens = list(
                self.get_author_tokens(authors, only_first_author=True)
            )
            if author_tokens:
                q += ((' ' if q != '' else '') + build_term('author', author_tokens))
        q = q.strip()
        if not q:
            return None

        url = self.BASE_URL + urlencode({'q': q})
        return url

    # }}}

    def download_cover(
        self,
        log,
        result_queue,
        abort,  # {{{
        title=None,
        authors=None,
        identifiers={},
        timeout=30,
        get_best_cover=False
    ):
        cached_url = self.get_cached_cover_url(identifiers)
        if cached_url is None:
            log.info('No cached cover found, running identify')
            rq = Queue()
            self.identify(
                log,
                rq,
                abort,
                title=title,
                authors=authors,
                identifiers=identifiers
            )
            if abort.is_set():
                return
            results = []
            while True:
                try:
                    results.append(rq.get_nowait())
                except Empty:
                    break
            results.sort(
                key=self.identify_results_keygen(
                    title=title, authors=authors, identifiers=identifiers
                )
            )
            for mi in results:
                cached_url = self.get_cached_cover_url(mi.identifiers)
                if cached_url is not None:
                    break
        if cached_url is None:
            log.info('No cover found')
            return

        if abort.is_set():
            return
        br = self.browser
        log('Downloading cover from:', cached_url)
        try:
            cdata = br.open_novisit(cached_url, timeout=timeout).read()
            if cdata:
                result_queue.put((self, cdata))
        except:
            log.exception('Failed to download cover from:', cached_url)

    # }}}

    def get_cached_cover_url(self, identifiers):  # {{{
        url = None
        db = identifiers.get('douban', None)
        if db is None:
            isbn = identifiers.get('isbn', None)
            if isbn is not None:
                db = self.cached_isbn_to_identifier(isbn)
        if db is not None:
            url = self.cached_identifier_to_cover_url(db)

        return url

    # }}}

    def get_all_details(
        self,
        br,
        log,
        entries,
        abort,  # {{{
        result_queue,
        timeout
    ):
        for relevance, i in enumerate(entries):
            try:
                ans = self.to_metadata(br, log, i, timeout)
                if isinstance(ans, Metadata):
                    ans.source_relevance = relevance
                    db = ans.identifiers['douban']
                    for isbn in getattr(ans, 'all_isbns', []):
                        self.cache_isbn_to_identifier(isbn, db)
                    if ans.has_douban_cover:
                        self.cache_identifier_to_cover_url(db, ans.has_douban_cover)
                    self.clean_downloaded_metadata(ans)
                    result_queue.put(ans)
            except:
                log.exception('Failed to get metadata for identify entry:', i)
            if abort.is_set():
                break

    # }}}

    def identify(
        self,
        log,
        result_queue,
        abort,
        title=None,
        authors=None,  # {{{
        identifiers={},
        timeout=30
    ):
        import json

        query = self.create_query(
            log, title=title, authors=authors, identifiers=identifiers
        )
        if not query:
            log.error('Insufficient metadata to construct query')
            return
        br = self.browser
        try:
            raw = br.open_novisit(query, timeout=timeout).read()
        except Exception as e:
            log.exception('Failed to make identify query: %r' % query)
            return as_unicode(e)
        try:
            j = json.loads(raw)
        except Exception as e:
            log.exception('Failed to parse identify results')
            return as_unicode(e)
        if 'items' in j:
            entries = j['items']
        else:
            entries = []
            entries.append(j)
        if not entries and identifiers and title and authors and \
                not abort.is_set():
            return self.identify(
                log,
                result_queue,
                abort,
                title=title,
                authors=authors,
                timeout=timeout
            )
        # There is no point running these queries in threads as douban
        # throttles requests returning 403 Forbidden errors
        self.get_all_details(br, log, entries, abort, result_queue, timeout)

        return None

    # }}}


if __name__ == '__main__':  # tests {{{
    # To run these test use: calibre-debug -e src/calibre/ebooks/metadata/sources/douban.py
    from calibre.ebooks.metadata.sources.test import (
        test_identify_plugin, title_test, authors_test
    )
    test_identify_plugin(
        Douban.name, [
            ({
                'identifiers': {
                    'isbn': '9787536692930'
                },
                'title': '三体',
                'authors': ['刘慈欣']
            }, [title_test('三体', exact=True),
                authors_test(['刘慈欣'])]),
            ({
                'title': 'Linux内核修炼之道',
                'authors': ['任桥伟']
            }, [title_test('Linux内核修炼之道', exact=False)]),
        ]
    )
# }}}
