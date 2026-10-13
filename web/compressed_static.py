"""Serve precompressed public UI assets only; API/media responses are untouched."""
from pathlib import Path
from starlette.datastructures import Headers
from starlette.responses import FileResponse
from starlette.staticfiles import StaticFiles, NotModifiedResponse

class PrecompressedStaticFiles(StaticFiles):
    async def get_response(self,path,scope):
        response=await super().get_response(path,scope)
        accept=Headers(scope=scope).get('accept-encoding','')
        accepts_gzip=False
        for part in accept.lower().split(','):
            bits=[bit.strip() for bit in part.split(';')]
            if bits[0]!='gzip':continue
            try:quality=float(next((bit[2:] for bit in bits[1:] if bit.startswith('q=')),'1'))
            except ValueError:quality=0
            accepts_gzip=quality>0
        original=getattr(response,'path',None)
        if not accepts_gzip or not original or response.status_code!=200:return response
        original=Path(original)
        if original.suffix not in {'.html','.js','.css'}:return response
        compressed=Path(str(original)+'.gz')
        if not compressed.is_file() or compressed.stat().st_mtime_ns<original.stat().st_mtime_ns:return response
        result=FileResponse(compressed,media_type=response.media_type,headers={'Content-Encoding':'gzip','Vary':'Accept-Encoding','Cache-Control':'no-cache'})
        # FileResponse populates ETag when constructed with stat_result.
        result.set_stat_headers(compressed.stat())
        if self.is_not_modified(result.headers,Headers(scope=scope)):return NotModifiedResponse(result.headers)
        return result
