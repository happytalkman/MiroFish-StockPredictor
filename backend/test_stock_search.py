from io import BytesIO
from zipfile import ZipFile
from pathlib import Path
import subprocess
import pytest
from app import create_app
from app.services import stock_catalog as catalog

@pytest.fixture(autouse=True)
def clear_cache():
    catalog._cache.clear()
    catalog._last_fail.clear()
    yield
    catalog._cache.clear()
    catalog._last_fail.clear()

@pytest.mark.parametrize('market,tail', [('KOSPI',228),('KOSDAQ',222)])
def test_download_parses_published_format(monkeypatch, market, tail):
    row = '005930   KR7005930003' + '삼성전자'.ljust(40) + 'ST' + '0'*(tail-3) + '\n'
    data=BytesIO()
    with ZipFile(data,'w') as archive:
        archive.writestr(market.lower()+'_code.mst',row.encode('cp949'))
    class Response:
        content=data.getvalue()
        def raise_for_status(self): pass
    def get(url, **kwargs):
        assert kwargs['timeout'] == (5,10)
        return Response()
    monkeypatch.setattr(catalog.requests,'get',get)
    assert catalog._download(market)==[{'ticker':'005930','name':'삼성전자','market':market}]

@pytest.mark.parametrize('query', ['삼성전자','005930','삼성'])
def test_search_name_code_and_cache(monkeypatch,query):
    calls=[]
    def download(market):
        calls.append(market)
        return [{'ticker':'005930','name':'삼성전자','market':market}]
    monkeypatch.setattr(catalog,'_download',download)
    assert catalog.search_catalog(query,'KOSPI')[0]['ticker']=='005930'
    assert catalog.search_catalog('not-a-stock','KOSPI')==[]
    assert calls==['KOSPI']

@pytest.fixture
def client():
    return create_app().test_client()

def test_upstream_failure_is_not_empty_success(monkeypatch,client):
    def fail(market): raise OSError('connection failed')
    monkeypatch.setattr(catalog,'_download',fail)
    monkeypatch.setattr(catalog,'_load_disk',lambda market:None)
    monkeypatch.setattr(catalog,'_seed',lambda market:[])
    response=client.get('/api/stock/search?query=005930')
    assert response.status_code==503
    assert response.json['success'] is False
    assert response.json['error']

def test_search_endpoint(monkeypatch,client):
    monkeypatch.setattr(catalog,'_download',lambda market:[{'ticker':'005930','name':'삼성전자','market':market}])
    response=client.get('/api/stock/search',query_string={'query':'삼성전자','market':'KOSPI'})
    assert response.status_code==200
    assert response.json['data']==[{'ticker':'005930','name':'삼성전자','market':'KOSPI'}]
    assert client.get('/api/stock/search?query=x&market=INVALID').status_code==400
    assert client.get('/api/stock/search?query=x&limit=-1').status_code==400

def test_frontend_calls_registered_routes():
    source=Path(__file__).resolve().parents[1]/'frontend/src/api/stock.js'
    script=r'''
const fs=require('node:fs');
const calls=[];
globalThis.stockTestApi={get:(...args)=>calls.push(args),post:(...args)=>calls.push(args)};
const source=fs.readFileSync(process.argv[1],'utf8').replace("import api from './index'",'const api=globalThis.stockTestApi');
(async()=>{
const api=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
api.searchStock('삼성전자');api.analyzeStock('005930','삼성전자');api.getAnalyzeStatus('a');api.startSimulation('a');api.getSimulationStatus('b');api.generateReport('b');api.getReport('c');
require('node:assert/strict').deepEqual(calls.map(c=>c[0]),['/api/stock/search','/api/stock/analyze','/api/stock/analyze/a/status','/api/stock/simulate','/api/stock/simulate/b/status','/api/stock/report/b','/api/stock/report/c']);
})();
'''
    subprocess.run(['node','-e',script,str(source)],check=True)
