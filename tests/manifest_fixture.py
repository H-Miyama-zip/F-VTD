"""Generate real validated fictitious ledger data; never modifies repository data."""
import contextlib
import io
import json
import shutil
import sys
from pathlib import Path
from test_ledger import Fixture, ROOT, row, ledger, build_site, publication_record, publications

def generate(output=None):
    f=Fixture()
    f.setUp()
    try:
        added=row('かくうついか','架空追加','added')
        url_row=dict(f.a,source_url='https://example.test/'+ 'long-path/'*40)
        note_row=dict(url_row,note='<img src=x onerror=alert(1)> 架空注記 '+ '長い注記'*50)
        fix=f.change('url-only','correct',f.a,url_row)
        note=f.change('note-only','correct',url_row,note_row)
        annotation=f.change('annotation','annotate',None,None)
        annotation['related_ids']=['url-only']
        annotation['metadata_before']={k:fix[k] for k in ledger.META}
        annotation['metadata_after']=dict(annotation['metadata_before'],reason='架空の記録訂正後理由')
        undo=f.change('undo-note','undo',note_row,url_row)
        undo['related_ids']=['note-only']
        f.update([f.change('add','add',None,added),fix,note,f.change('delete','delete',f.b,None),annotation,undo])
        f.write_master([url_row,added])
        with contextlib.redirect_stdout(io.StringIO()):
            version,manifest,files=f.build()
            if output:
                shutil.copytree(ROOT/'site',f.root/'site',dirs_exist_ok=True)
                config=json.loads((f.root/'site/config.json').read_bytes());config['demo']=True
                (f.root/'site/config.json').write_bytes(ledger.canonical(config))
                # Only this isolated fixture has a fictitious publication.
                publications.append(publication_record(version,files),f.root,release_bytes=files['release.json'],zip_bytes=files[f'F-VTD-{version}.zip'])
                build_site.main(f.root,Path(output))
                manifest=json.loads((Path(output)/'data/latest.json').read_bytes())
        return manifest
    finally:
        f.git_patch.stop()
        f.temp.cleanup()

if __name__=='__main__':
    print(json.dumps(generate(sys.argv[1] if len(sys.argv)>1 else None),ensure_ascii=True))
