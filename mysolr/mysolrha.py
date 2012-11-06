# -*- coding: utf-8 -*-
"""
mysolr.mysolr
~~~~~~~~~~~~~

This module impliments the mysolr Solr class with software High Availability methods, providing an easy access to
operate with a group of Solr servers to read or write.

>>> from mysolrha import Solr
>>> solr = Solr({'http://myserver:8080/solr':{'write':True,read':True},'http://backupserver:80/solr':{'write':True,'read':True}})
>>> query = {'q':'*:*', 'rows': 0, 'start': 0, 'facet': 'true', 
             'facet.field': 'province'}
>>> query_response = solr.search(**query)

"""
from .response import SolrResponse
from .compat import urljoin, get_wt, compat_args, get_basestring
from xml.sax.saxutils import escape

import json
import requests

class Solr(object):
    
    """Acts as an easy-to-use interface to Solr."""
    def HA(need_write=False):
        def wrap(fn):
            def exception_wrapper(*args, **kwargs):
                #print("Entrando en HADecorator. Lanzando:%s"%str(fn.__name__))
                #print("HADecorator args:%s "%str(dir(*args)))
                #print("HADecorator kwargs:%s "%str(kwargs))
                #print("HADecorator need_write Argument: %s"%(need_write))
                try:
                    out = fn(*args, **kwargs)
                    return out
                except requests.exceptions.ConnectionError as exception:
                    args[0]._get_working_url(Write=need_write)
                    #print("Inside the HADecorator new base_url selected %s"%args[0].base_url)
                    out = fn(*args, **kwargs)
                    return out
                except:
                    pass
            return exception_wrapper
        return wrap

    def __init__(self, base_url={'http://localhost:8080/solr/':{'write':True}}, auth=None,
                 version=None):
        """ Initializes a Solr object. Solr URL is a needed parameter.

        :param base_url: Url to solr index dictionary(Write and Read must be true in this version)
        :param readers: Dictionary with info about the 
        :param auth: Described in requests documentation:
                     http://docs.python-requests.org/en/latest/user/quickstart/#basic-authentication 
        :param version: first number of the solr version. i.e. 4 if solr 
                        version is 4.0.0 If you set to none this parameter
                        a request to admin/system will be done at init time
                        in order to guess the version.
        """
        self.solrs = base_url
        self.is_writer = False
        self.auth = auth
        self.base_url = None
        self._get_working_url(Write=True)
        #print("Base_URL %s"%self.base_url)
        self.version = version
        if not version:
            self.version = self.get_version()
        assert(self.version in (1, 3, 4))

    @HA(need_write=False)
    def search(self, resource='select', **kwargs):
        """Queries Solr with the given kwargs and returns a SolrResponse
        object.

        :param resource: Request dispatcher. 'select' by default.
        :param **kwargs: Dictionary containing any of the available Solr query
                         parameters described in
                         http://wiki.apache.org/solr/CommonQueryParameters.
                         'q' is a mandatory parameter.

        """
        query = build_request(kwargs)
        
        #try:
        http_response = requests.get(urljoin(self.base_url, resource),
                                    params=query, auth=self.auth)
        #except:
        #    _get_working_url(Write=self.is_writer)
        #    return(search(resource=resource,kwargs))

        solr_response = SolrResponse(http_response)
        return solr_response

    def search_cursor(self, resource='select', **kwargs):
        """ """
        query = build_request(kwargs)
        cursor = Cursor(urljoin(self.base_url, resource), query, self.auth)

        return cursor
    
    def async_search(self, queries, size=10, resource='select'):
        """ Asynchronous search using async module from requests. 

        :param queries:  List of queries. Each query is a dictionary containing
                         any of the available Solr query parameters described in
                         http://wiki.apache.org/solr/CommonQueryParameters.
                         'q' is a mandatory parameter.
        :param size:     Size of threadpool
        :param resource: Request dispatcher. 'select' by default.
        """
        try:
            import grequests
        except:
            raise RuntimeError('grequests is required for Solr.async_search.')

        url = urljoin(self.base_url, resource)
        queries = map(build_request, queries)
        rs = (grequests.get(url, params=query) for query in queries)
        responses = grequests.map(rs, size=size)
        return [SolrResponse(http_response) for http_response in responses]

    @HA(need_write=True)
    def update(self, documents, input_type='json', commit=True):
        """Sends an update/add message to add the array of hashes(documents) to
        Solr.

        :param documents: A list of solr-compatible documents to index. You
                          should use unicode strings for text/string fields.
        :param input_type: The format which documents are sent. Remember that
                           json is not supported until version 3.
        :param commit: If True, sends a commit message after the operation is
                       executed.

        """
        assert input_type in ['xml', 'json']

        if not self.is_writer:
            self._get_working_url(Write=True)

        if input_type == 'xml':
            http_response = self._post_xml(_get_add_xml(documents))
        else:
            http_response = self._post_json(json.dumps(documents))
        if commit:
            self.commit()
        
        return SolrResponse(http_response)

    def delete_by_key(self, identifier, commit=True):
        """Sends an ID delete message to Solr.

        :param commit: If True, sends a commit message after the operation is
                       executed.

        """
        xml = '<delete><id>%s</id></delete>' % (identifier)
        http_response = self._post_xml(xml)
        if commit:
            self.commit()
        return SolrResponse(http_response)

    def delete_by_query(self, query, commit=True):
        """Sends a query delete message to Solr.

        :param commit: If True, sends a commit message after the operation is
                       executed.

        """
        xml = '<delete><query>%s</query></delete>' % (query)
        http_response = self._post_xml(xml)
        if commit:
            self.commit()
        return SolrResponse(http_response)

    def commit(self, wait_flush=True,
               wait_searcher=True, expunge_deletes=False):
        """Sends a commit message to Solr.

        :param wait_flush: Block until index changes are flushed to disk
                           (default is True).
        :param wait_searcher: Block until a new searcher is opened and
                              registered as the main query searcher, making the
                              changes visible (default is True).
        :param expunge_deletes: Merge segments with deletes away (default is 
                                False)

        """
        xml = '<commit '
        if self.version < 4:
            xml += 'waitFlush="%s" ' % str(wait_flush).lower()
        xml += 'waitSearcher="%s" ' % str(wait_searcher).lower()
        xml += 'expungeDeletes="%s" ' % str(expunge_deletes).lower()
        xml += '/>'

        http_response = self._post_xml(xml)
        return SolrResponse(http_response)

    def optimize(self, wait_flush=True, wait_searcher=True, max_segments=1):
        """Sends an optimize message to Solr.

        :param wait_flush: Block until index changes are flushed to disk
                           (default is True)
        :param wait_searcher: Block until a new searcher is opened and
                              registered as the main query searcher, making the
                              changes visible (default is True)
        :param max_segments: Optimizes down to at most this number of segments
                             (default is 1)

        """
        xml = '<optimize '
        if self.version < 4:
            xml += 'waitFlush="%s" ' % str(wait_flush).lower()
        xml += 'waitSearcher="%s" ' % str(wait_searcher).lower()
        xml += 'maxSegments="%s" ' % max_segments
        xml += '/>'

        http_response = self._post_xml(xml)
        return SolrResponse(http_response)

    def rollback(self):
        """Sends a rollback message to Solr server."""
        xml = '<rollback />'
        http_response = self._post_xml(xml)
        return SolrResponse(http_response)

    def ping(self,solr_url=None):
        """ Ping call to solr server. """
        solr_url if solr_url else self.base_url
        url = urljoin(solr_url, 'admin/ping')
        print("Enel ping la url es \"%s\""%url)
        http_response = requests.get(url, params={'wt': 'json'}, auth=self.auth)
        print("HTTP_RESPONSE:%s"%str(http_response))
        return SolrResponse(http_response)

    def is_up(self):
        """Check if a Solr server is up using ping call"""
        try:
            solr_response = self.ping()
        except:
            return False
        return solr_response.status == 200 and solr_response.solr_status == 0

    def schema(self):
        return self._get_file('schema.xml')

    def solrconfig(self):
        return self._get_file('solrconfig.xml')

    def get_system_info(self):
        """ Gets solr system status. """
        url = urljoin(self.base_url, 'admin/system')
        params = {'wt': get_wt()}
        http_response = requests.get(url, params=params, auth=self.auth)
        return SolrResponse(http_response)

    def get_version(self):
        system_info = self.get_system_info()
        version = system_info.raw_content['lucene']['solr-spec-version']
        return int(version[0])

    def more_like_this(self, resource='mlt', text=None, **kwargs):
        """Implements convenient access to Solr MoreLikeThis functionality  

        Please, visit http://wiki.apache.org/solr/MoreLikeThis to learn more
        about MLT configuration and common parameters.

        There are two ways of using MLT in Solr:

        Using a previously configured RequestHandler
            You normally specify a query and the first matching document for 
            that query is used to retrieve similar documents.
            You can however specify a text instead of a query, and similar
            documents to the text will be returned.
            You must configure a MLT RequestHandler in your solrconfig.xml in
            order to get advantage of this functionality.
            Note that this method has a default resource name with value "mlt",
            but if your RequestHandler has a different name you must specify it
            when calling the more_like_this method.

        Using the MLT Search Component:
            The resulting documents in this case will be those that match the
            regular query, but the SolrResponse will have a "mlt" section where
            similar documents for each result document will be given.

        :param resource: Request dispatcher. 'ml' by default.
        :param text: Text to use for similar documents retrieval. None by
                     default.
        :param **kwargs: Dictionary containing any of the available Solr query
                         parameters described in
                         http://wiki.apache.org/solr/CommonQueryParameters
                         or MoreLikeThis Common parameters described in
                         http://wiki.apache.org/solr/MoreLikeThis.
                         'q' is a mandatory parameter in all cases except
                         when using a MLT RequestHandler with a Text parameter.
    
        """
        if text is not None: #RequestHandler with Content-Streamed Text
            #we dont call build_query because 'q' is NOT mandatory in this case
            kwargs['wt'] = get_wt()
            headers = {'Content-type': 'text/json'}
            http_response = requests.post(urljoin(self.base_url, resource), 
                                          params=kwargs,
                                          data=text,
                                          headers=headers,
                                          auth=self.auth)
            solr_response = SolrResponse(http_response)
            return solr_response
        else:
            return self.search(resource=resource, **kwargs)

    def _post_xml(self, xml):
        """ Sends the xml to Solr server.

        :param xml: XML document to be posted.
        """
        url = urljoin(self.base_url, 'update')
        xml_data = xml.encode('utf-8')
        headers = {
            'Content-type': 'text/xml; charset=utf-8',
            'Content-Length': "%s" % len(xml_data)
        }
        http_response = requests.post(url, data=xml_data,
                                      headers=headers, auth=self.auth)
        return http_response

    def _post_json(self, json_doc):
        """ Sends the json to Solr server.

        :param json_doc: JSON document to be posted.
        """
        url = urljoin(self.base_url, 'update/json')
        json_data = json_doc.encode('utf-8')
        headers = {
            'Content-type': 'application/json; charset=utf-8',
            'Content-Length': "%s" % len(json_data)
        }
        http_response = requests.post(url, data=json_data,
                                      headers=headers, auth=self.auth)
        return http_response

    def _get_file(self, filename):
        """Retrieves config files of the current index."""
        url = urljoin(self.base_url, 'admin/file')
        params = {
            'contentType': 'text/xml;charset=utf-8',
            'file' : filename
        }
        http_response = requests.get(url, params=params, auth=self.auth)
        return http_response.content

    def _get_working_url(self,Write=False):
        """Replace in the self.base_url the actual one by other of the list who replies to ping
        :param Write: If the url must be one with write permisions in the list.
        """
        for url in list(self.solrs.keys()):
            #print(url)
            self.is_writer = self.solrs[url]['write']
            # base_url must be end with /
            if url[-1] != '/':
                url += '/'
            test = urljoin(url, 'admin/ping')
            try:
                solr_response = requests.get(url, params={'wt': 'json'}, auth=self.auth)
                #print("Conectado al solr:%s"%url)
                if (Write):
                    if self.is_writer:
                        self.base_url = url
                    else:
                        continue
                else:
                    self.base_url = url
                break
            except Exception as e:
                #print("Fallo conectando al solr %s"%url)
                #print(e)
                continue


class Cursor(object):
    """ Implements the concept of cursor in relational databases """
    def __init__(self, url, query, auth=None):
        """ Cursor initialization """
        self.url = url
        self.query = query
        self.auth = auth

    def fetch(self, rows=None):
        """ Generator method that grabs all the documents in bulk sets of 
        'rows' documents

        :param rows: number of rows for each request
        """
        if rows:
            self.query['rows'] = rows

        if 'rows' not in self.query:
            self.query['rows'] = 10

        self.query['start'] = 0

        end = False
        docs_retrieved = 0
        while not end:
            http_response = requests.get(self.url, params=self.query,
                                         auth=self.auth)
            solr_response = SolrResponse(http_response)
            yield solr_response
            total_results = solr_response.total_results
            docs_retrieved += len(solr_response.documents)
            end = docs_retrieved == total_results
            self.query['start'] += self.query['rows']


def _get_add_xml(array_of_hash, overwrite=True):
    """ Creates add XML message to send to Solr based on the array of hashes
    (documents) provided.

    :param overwrite: Newer documents will replace previously added documents
                      with the same uniqueKey (default is True)

    """
    xml = '<add overwrite="%s">' % ('true' if overwrite else 'false')
    for doc_hash in array_of_hash:
        doc = '<doc>'
        for key, value in doc_hash.items():
            if isinstance(value, list):
                for v in value:
                    if isinstance(v, get_basestring()):
                        v = escape(v)
                    doc += '<field name="%s">%s</field>' % (key, v)
            else:
                if isinstance(value, get_basestring()):
                    value = escape(value)
                doc += '<field name="%s">%s</field>' % (key, value)
        doc += '</doc>'
        xml += doc
    xml += '</add>'
    return xml


def  build_request(query):
    """ Check solr query and put convenient format """
    assert 'q' in query
    compat_args(query)
    query['wt'] = get_wt()
    return query


