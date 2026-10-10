"""Process a frozen publication through the authenticated gateway."""
import argparse, json, os, re
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

def request(route, body=None, content_type='application/json'):
    base=os.environ['GENEALOGY_API'].rstrip('/')
    if not base.startswith('https://') and not base.startswith('http://127.0.0.1:'):
        raise ValueError('Use HTTPS or a local tunnel')
    req=Request(base+route, data=body, headers={'Authorization':'Bearer '+os.environ['GENEALOGY_TOKEN'],'Content-Type':content_type})
    with urlopen(req,timeout=90) as response:
        data=response.read()
        return data if response.headers.get_content_type()!='application/json' else json.loads(data)

def main():
    parser=argparse.ArgumentParser()
    commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('next')
    fetch=commands.add_parser('fetch'); fetch.add_argument('revision'); fetch.add_argument('output',type=Path)
    books=commands.add_parser('books'); books.add_argument('revision'); books.add_argument('report',type=Path)
    result=commands.add_parser('result'); result.add_argument('revision'); result.add_argument('target',choices=['books','geni']); result.add_argument('state',choices=['running','blocked','failed','ready']); result.add_argument('evidence')
    args=parser.parse_args()
    if args.command=='next':
        print(json.dumps(request('/api/master/publications/next/'),ensure_ascii=False)); return
    if not re.fullmatch('[a-f0-9]{64}',args.revision):raise ValueError('Invalid revision')
    route='/api/master/publications/'+args.revision+'/'
    if args.command=='fetch':
        data=request(route+'snapshot/')
        if args.output.exists():raise ValueError('Choose an unused snapshot path')
        args.output.write_bytes(data); print('Frozen GEDCOM downloaded'); return
    if args.command=='result':
        if args.target=='books' and args.state=='ready':raise ValueError('Use the books command after verification')
        payload=dict(target=args.target,state=args.state,detail=args.evidence)
        request(route+'result/',json.dumps(payload).encode()); print('Evidence recorded'); return
    report=json.loads(args.report.read_text(encoding='utf-8'))
    if report.get('canonical_revision')!=args.revision:raise ValueError('Book report belongs to another GEDCOM revision')
    if not report.get('checks') or not all(v is True for v in report['checks'].values()):raise ValueError('Book acceptance checks failed')
    volumes=report['volumes']
    if set(volumes)!=set('123456'):raise ValueError('Provide six volumes')
    manifest=[]; root=args.report.resolve().parent
    for number in '123456':
        volume=volumes[number]; file=(root/volume['a4_pdf']).resolve()
        if not file.is_relative_to(root):raise ValueError('Book path leaves report folder')
        data=file.read_bytes()
        if not data.startswith(b'%PDF-'):raise ValueError('Invalid PDF')
        uploaded=request(route+'books/'+quote(file.name,safe=''),data,'application/pdf')
        manifest.append(dict(file=uploaded['file'],title=volume['title'],pages=volume['a4_pages']))
    request(route+'books/',json.dumps({'books':manifest},ensure_ascii=False).encode()); print('Six verified books installed')

if __name__=='__main__':main()
