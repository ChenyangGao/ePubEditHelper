#! /usr/bin/env python3
# coding: utf-8
__author__  = 'ChenyangGao <https://chenyanggao.github.io/>'
__version__ = (0, 4, 1)


from argparse import ArgumentParser, RawTextHelpFormatter
from generate_method import BASE4CHARS, NAME_GENERATORS


METHODS_LIST = list(NAME_GENERATORS.values())
METHODS_DOC  = '\n'.join(
    f'[{i}] {n}:\n    {m.__doc__}' 
    for i, (n, m) in enumerate(NAME_GENERATORS.items()))

# TODO: 添加一个命令行参数，只有在文件名满足一定的模式的情况下才进行改名
ap = ArgumentParser(
    description='对 ePub 内在 OPF 文件所在文件夹或子文件夹下的文件修改文件名',
    formatter_class=RawTextHelpFormatter,
)
ap.add_argument('-rm', '--remove-encrypt-file', dest='remove_encrypt_file', action='store_true', 
                help='移除加密文件 META-INF/encryption.xml')
ap.add_argument('-ad', '--add-encrypt-file', dest='add_encrypt_file', action='store_true', 
                help='添加加密文件 META-INF/encryption.xml。如果已有加密文件，但未指定'
                        '-rm 或 --remove-encrypt-file，则忽略。')
ap.add_argument('-l', '--epub-list', dest="list", nargs='+', 
                help='待处理的 ePub 文件（有多个用空格隔开）')
# TODO: 以后还会加入对 OPS 文件内 item 元素的 id 值进行正则表达式筛选
ap.add_argument('-s', '--scan-dirs', dest="scan_dirs", nargs='*', 
                help='在 OPF 文件所在文件夹内，会对传入的这组路径内的文件夹及其子文件夹内的文件会被重命名，'
                        '如果不指定此参数（相当于传入 \'.\' 或 \'\'）则扫描 OPF 文件所在文件夹下所有文件夹，'
                        '但如果只指定，却不传任何参数，则不会对文件进行改名（这适用于只想添加或移除加密文件）。'
                        # TODO: 增加扩展语法，提供模式匹配
                        #'\n我更提供了一下扩展语法：\n'
                        #'    1) pattern      搜索和 pattern 相等的文件夹路径\n'
                        #'    2) str:pattern  等同于 1)，搜索和 pattern 相等的文件夹路径\n'
                        #'    3) glob:pattern 把 pattern 视为 glob 模式，搜索和 pattern 相等的文件夹路径\n'
                        #'    4) re:pattern   把 pattern 视为 正则表达式 模式，搜索和 pattern 相等的文件夹路径\n'
                )
ap.add_argument('-r', '--recursive', action='store_true', 
                help='如果不指定，遇到文件夹时，只扫描这个文件夹内所有.epub 结尾的文件。'
                        '如果指定，遇到文件夹时，会遍历这个文件夹及其所有子文件夹（如果有的话）'
                        '下所有 .epub 结尾的文件。')
ap.add_argument('-g', '--glob', action='store_true', 
                help='如果指定，则把 -l 参数传入的路径当成 glob 查询模式，如果再指定-r，'
                        '** 会匹配任何文件和任意多个文件夹或子文件夹')
ap.add_argument('-raf', '--reset-method-after-files-processed', 
                dest='reset_method_after_files_processed', action='store_true', 
                help='每处理完一个文件，就对产生文件名的函数进行重置')
ap.add_argument('-m', '--method', default='0', 
                help='产生文件名的策略 （输入数字或名字，默认值 0）\n' + METHODS_DOC)
ap.add_argument('-n', '--encode-filenames', dest='encode_filenames', action='store_true', 
                help='对文件名用一些字符的可重排列进行编码')
ap.add_argument('-ch', '--chars', default=BASE4CHARS, 
                help='用于编码的字符集（不可重复，字符集大小应该是2、4、16、256之一），'
                        '如果你没有指定 -n 或 --encode_filenames，此参数被忽略，默认值是 '
                        + BASE4CHARS)
ap.add_argument('-q', '--quote-names', dest='quote_names', action='store_true', 
                help='对改动的文件名进行百分号 %% 转义')
ap.add_argument('-x', '--suffix', default='-repack', 
                help='已处理的 ePub 文件名为在原来的 ePub 文件名的扩展名前面添加后缀，默认值是 -repack')


def parse_argv(argv):
    return ap.parse_args(argv)


if __name__ == '__main__':
    from sys import argv
    if '-h' in argv or '--help' in argv:
        parse_argv(['-h'])


import posixpath

from os import path
from re import compile as re_compile
from typing import (
    Callable, Collection, Dict, List, Optional, Tuple, Union
)
from urllib.parse import quote, unquote
from xml.etree.ElementTree import fromstring
from zipfile import ZipFile, ZipInfo

from util.path import relative_path, add_stem_suffix
from generate_method import make_generator, make_bcp_generator


PROJECT_FOLDER = path.dirname(__file__)
SRC_FOLDER = path.join(PROJECT_FOLDER, 'src')

CRE_NAME = re_compile(r'(?P<name>.*?)(?P<append>~[_0-9a-zA-Z]+)?(?P<suffix>\.[_0-9a-zA-z]+)')
CRE_PROT = re_compile(r'\w+:/')
CRE_LINK = re_compile(r'([^#?]+)(.*)')
CRE_HREF = re_compile(r'(<[^/][^>]+\bhref=")(?P<link>[^>"]+)')
CRE_SRC  = re_compile(r'(<[^/][^>]+\bsrc=")(?P<link>[^>"]+)')
CRE_URL  = re_compile(r'\burl\(\s*(?:"(?P<dlink>(?:[^"]|(?<=\\)")+)"|\'(?P<slink>(?:[^\']|(?<=\\)\')+)\'|(?P<link>[^)]+))\s*\)')


def get_elnode_attrib(elnode) -> dict:
    '获取一个 xml / xhtml 标签的属性值'
    if isinstance(elnode, (bytes, str)):
        elnode = fromstring(elnode)
    return elnode.attrib


def get_opf_path(
    src_epub: ZipFile, _cre=re_compile('full-path="([^"]+)')
) -> str:
    '''获取 ePub 文件中的 OPF 文件的路径
    该路径可能位于 META-INF/container.xml 文件的这个 xpath 路径下
        /container/rootfiles/rootfile/@full-path
    所以我尝试直接根据元素的 full-path 属性来判断，但这可能不是普遍适用的
    '''
    content = unquote(
        src_epub.read('META-INF/container.xml').decode())
    match = _cre.search(content)
    if match is None:
        raise Exception('OPF file path not found')
    return match[1]


def get_opf_itemmap(
    src_epub: ZipFile, 
    opf_path: Union[str, ZipInfo, None] = None,
    _cre=re_compile('<item .*?/>'),
) -> dict:
    '读取 OPF 文件的所有 item 标签，返回 href: item 标签属性的字典'
    if opf_path is None:
        opf_path = get_opf_path(src_epub)
    opf = unquote(src_epub.read(opf_path).decode())
    return {
        attrib['href']: attrib
        for attrib in map(get_elnode_attrib, _cre.findall(opf))
        if attrib.get('href')
    }


def make_repl_map(
    itemmap: dict, 
    generate: Callable[..., str], 
    scan_dirs: Optional[Tuple[str, ...]] = None, 
    quote_names: bool = False, 
) -> Tuple[dict, list]:
    '基于 OPF 文件的 href 替换映射，键是原来的 href，值是修改后的 href'
    repl_map: Dict[str, str] = {}
    key_map:  Dict[str, str] = {}

    for href, attrib in itemmap.items():
        if href == 'toc.ncx':
            continue
        if scan_dirs is not None:
            if not href.startswith(scan_dirs):
                continue

        href_dir = posixpath.dirname(href)
        parts = href.split('/')
        name = parts[-1]
        name_dict = CRE_NAME.fullmatch(name).groupdict()
        key = (href_dir, name_dict['name'], name_dict['suffix'])

        # 据说在多看阅读，封面图片可以有 2 个版本，形如 cover.jpg 和 cover~slim.jpg，
        # 其中 cover.jpg 适用于 4:3 屏，cover~slim.jpg 适用于 16:9 屏。
        # 由于遇到上面这种设计，我不知道是不是还有类似设计，所以我用一个正则表达式，
        # 匹配扩展名前的 ~[_0-9a-zA-Z]+ 部分，当成是一种特殊的后缀，为此我特意增加了一组逻辑，
        # 如果两个文件名只有这种后缀部分不同，那么改名后也保证只有这种后缀部分不同，
        # 比如上述的封面图片，被改名后，会变成形如 newname.jpg 和 newname~slim.jpg
        if key in key_map:
            generate_name = key_map[key]
        else:
            generate_name = key_map[key] = generate(attrib)

        suffix = name_dict['suffix']
        if generate_name.endswith(name_dict['suffix']):
            suffix = ''

        newname = '%s%s%s' % (generate_name, name_dict['append'] or '', suffix)
        if len(parts) > 1:
            newname = posixpath.join(parts[0], newname)
        if quote_names:
            newname = quote(newname)
        repl_map[href] = newname

    return repl_map


def rename_in_epub(
    epub_path: str, 
    generate: Callable[..., str] = lambda attrib: attrib['id'],
    stem_suffix: str = '-repack',
    quote_names: bool = False,
    remove_encrypt_file: bool = False,
    add_encrypt_file: bool = False,
    scan_dirs: Optional[Collection[str]] = None,
) -> str:
    '对 ePub 内在 OPF 文件所在文件夹或子文件夹下的文件修改文件名'
    epub_path2 = add_stem_suffix(epub_path, stem_suffix)
    has_encrypt_file: bool = False
    is_empty_scan_dirs = scan_dirs == []

    def normalize_dirname(dir_: str, _cre=re_compile(r'^\.+/')) -> str:
        if dir_.startswith('.'):
            dir_ = _cre.sub('', dir_, 1)
        if not dir_.endswith('/'):
            dir_ += '/'
        return dir_

    def css_repl(m):
        md = m.groupdict()
        if md['dlink']:
            link = unquote(md['dlink'])
        elif md['slink']:
            link = unquote(md['slink'])
        elif md['link']:
            link = unquote(md['link'])
        else:
            return m[0]

        if link.startswith(('#', '/')) or CRE_PROT.match(link) is not None:
            return m[0]

        uri, suf = CRE_LINK.fullmatch(link).groups()
        full_uri = relative_path(uri, opf_href, lib=posixpath)
        if full_uri in repl_map:
            return 'url("%s%s%s")' % (advance_str, repl_map[full_uri], suf)
        else:
            return m[0]

    def hxml_repl(m):
        link = unquote(m['link'])
        if link.startswith(('#', '/')) or CRE_PROT.match(link) is not None:
            return m[0]

        uri, suf = CRE_LINK.fullmatch(link).groups()
        full_uri = relative_path(uri, opf_href, lib=posixpath)
        if full_uri in repl_map:
            return m[1] + advance_str + repl_map[full_uri] + suf
        else:
            return m[0]

    if scan_dirs is not None:
        if '.' in scan_dirs or '' in scan_dirs:
            scan_dirs = None
        else:
            scan_dirs = tuple(map(normalize_dirname, scan_dirs))

    with ZipFile(epub_path, mode='r') as src_epub, \
            ZipFile(epub_path2, mode='w') as tgt_epub:
        opf_path = get_opf_path(src_epub)
        opf_root, opf_name = posixpath.split(opf_path)
        opf_root += '/'
        opf_root_len = len(opf_root)

        itemmap = get_opf_itemmap(src_epub, opf_path)
        repl_map = make_repl_map(
            itemmap=itemmap, 
            generate=generate,
            scan_dirs=scan_dirs,
            quote_names=quote_names,
        )

        for zipinfo in src_epub.filelist:
            if zipinfo.is_dir():
                continue # ignore directories

            zi_filename: str = zipinfo.filename

            if zi_filename == 'META-INF/encryption.xml':
                if remove_encrypt_file:
                    continue
                else:
                    has_encrypt_file = True

            if not zi_filename.startswith(opf_root):
                tgt_epub.writestr(zipinfo, src_epub.read(zipinfo))
                continue

            opf_href: str = zi_filename[opf_root_len:]  
            if opf_href not in itemmap and opf_href != opf_name:
                print('⚠️ 跳过文件', zi_filename, 
                      '，因为它未在 %s 内被列出' % opf_path)
                continue

            if is_empty_scan_dirs:
                content = src_epub.read(zipinfo)
                zipinfo.file_size = len(content)
                tgt_epub.writestr(zipinfo, content)
                continue

            is_opf_file = opf_href == opf_name

            advance_str = ''
            mimetype = None
            if opf_href in itemmap:
                item_attrib = itemmap[opf_href]
                mimetype = item_attrib['media-type']
                if opf_href in repl_map:
                    advance_str = '../' * (len(repl_map[opf_href].split('/')) - 1)

            content = src_epub.read(zipinfo)

            if is_opf_file or mimetype in ('text/css', 'text/html', 'application/xml', 
                                        'application/xhtml+xml', 'application/x-dtbncx+xml'):
                text = content.decode('utf-8')
                if is_opf_file or mimetype != 'text/css':
                    text_new = CRE_HREF.sub(hxml_repl, text)
                    text_new = CRE_SRC.sub(hxml_repl, text_new)
                else:
                    text_new = CRE_URL.sub(css_repl, text)
                if text != text_new:
                    content = text_new.encode('utf-8')
                    zipinfo.file_size = len(content)

            zipinfo.filename = opf_root + unquote(repl_map.get(opf_href, opf_href))
            tgt_epub.writestr(zipinfo, content)

        if add_encrypt_file and not has_encrypt_file:
            tgt_epub.write(
                path.join(SRC_FOLDER, 'encryption.xml'), 
                'META-INF/encryption.xml'
            )

    return epub_path2


def main(argv: Optional[List[str]] = None):
    args = parse_argv(argv)

    try:
        method = NAME_GENERATORS[args.method]
    except KeyError:
        method_index = int(args.method)
        method = METHODS_LIST[method_index]

    reset = None
    if args.reset_method_after_files_processed:
        reset = getattr(method, 'reset', None)

    if args.encode_filenames:
        method = make_bcp_generator(method, args.chars)
    else:
        method = make_generator(method)

    def process_file(epub):
        newfilename = rename_in_epub(
            epub, 
            scan_dirs=args.scan_dirs,
            stem_suffix=args.suffix, 
            quote_names=args.quote_names,
            generate=method,
            remove_encrypt_file=args.remove_encrypt_file,
            add_encrypt_file=args.add_encrypt_file,
        )
        print('产生文件：', newfilename)

    print('【接收参数】\n', args, '\n')
    print('【采用方法】\n', method.__name__, '\n')
    print('【方法说明】\n', method.__doc__, '\n')
    print('【处理结果】')
    if args.glob:
        from glob import iglob

        for epub_glob in args.list:
            for fpath in iglob(epub_glob, recursive=args.recursive):
                if path.isfile(fpath):
                    process_file(fpath)
                    if reset: reset()
    else:
        from util.path import iter_scan_files

        for epub in args.list:
            if not path.exists(epub):
                print('🚨 跳过不存在的文件或文件夹：', epub)
            elif path.isdir(epub):
                for fpath in iter_scan_files(epub, recursive=args.recursive):
                    if fpath.endswith('.epub'):
                        process_file(fpath)
                        if reset: reset()
            else:
                process_file(epub)
                if reset: reset()


if __name__ == '__main__':
    main()

