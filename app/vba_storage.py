"""Inspect source-only MS-OVBA storage before installing the Mac template.

No macro execution or changes to Office security settings. Compression and
records follow MS-OVBA sections 2.3.4 and 2.4.1. Host Word compiles the sources.
"""
import io
import re
import struct


def decompress(data):
    if not data or data[0] != 1:
        raise ValueError('Invalid VBA compressed container')
    output = bytearray()
    pos = 1
    while pos < len(data):
        start = len(output)
        header = struct.unpack_from('<H', data, pos)[0]
        end = pos + (header & 0xFFF) + 3
        if (header >> 12) & 7 != 3 or end > len(data):
            raise ValueError('Invalid VBA chunk')
        pos += 2
        if not header & 0x8000:
            if end-pos != 4096:
                raise ValueError('Invalid uncompressed VBA chunk')
            output.extend(data[pos:end]); pos = end
            continue
        while pos < end:
            flags = data[pos]; pos += 1
            for bit in range(8):
                if pos >= end:
                    break
                if flags & (1 << bit):
                    if pos+2 > end or len(output) == start:
                        raise ValueError('Invalid VBA copy token')
                    token = struct.unpack_from('<H', data, pos)[0]; pos += 2
                    width = max(4, (len(output)-start-1).bit_length())
                    length = (token & (0xFFFF >> width)) + 3
                    offset = (token >> (16-width)) + 1
                    if offset > len(output)-start:
                        raise ValueError('VBA copy crosses chunk boundary')
                    for _ in range(length):
                        output.append(output[-offset])
                else:
                    output.append(data[pos]); pos += 1
                if len(output)-start > 4096:
                    raise ValueError('VBA chunk expands beyond 4096 bytes')
    return bytes(output)


def compress_literals(data):
    """Emit standards-compliant compressed chunks containing literal tokens."""
    output = bytearray(b'\x01')
    for start in range(0, len(data), 3600):
        chunk = data[start:start+3600]
        payload = b''.join(b'\0'+chunk[p:p+8] for p in range(0,len(chunk),8))
        output.extend(struct.pack('<H',0xB000 | (len(payload)-1)))
        output.extend(payload)
    return bytes(output)


def records(data):
    """The bundled project uses ordinary records and PROJECTVERSION's extra U16."""
    pos = 0
    while pos < len(data):
        if pos+6 > len(data):
            raise ValueError('Truncated VBA directory record')
        kind, size = struct.unpack_from('<HI', data, pos)
        size = 6 if kind == 9 else size
        end = pos+6+size
        if end > len(data):
            raise ValueError('VBA directory record length exceeds stream')
        yield kind, pos, data[pos+6:end]
        pos = end


def portable_source(text):
    """Keep Chinese UI strings independent of the build machine's ANSI locale."""
    text = text.replace('\r\n','\n').replace('\r','\n')
    def literal(match):
        value = match.group()[1:-1].replace('""','"')
        if value.isascii():
            return match.group()
        codepoints = ' '.join('%04X' % ord(c) for c in value)
        return 'BRUnicode("'+codepoints+'")'
    text = re.sub(r'"(?:[^"\n]|"")*"', literal, text)
    text += ('\nPrivate Function BRUnicode(ByVal codes As String) As String\n'
             '    Dim token As Variant\n'
             '    For Each token In Split(codes, " ")\n'
             '        BRUnicode = BRUnicode & ChrW(CLng("&H" & token))\n'
             '    Next\nEnd Function\n')
    text.encode('ascii')
    if max(map(len,text.splitlines())) > 1000:
        raise ValueError('Portable VBA source exceeds line-length limit')
    return text.replace('\n','\r\n').encode('ascii')


def validate_storage(data, expected_source=None):
    import olefile
    with olefile.OleFileIO(io.BytesIO(data),raise_defects=olefile.DEFECT_INCORRECT) as storage:
        paths = storage.listdir()
        if any(p[-1].startswith('__SRP_') for p in paths):
            raise ValueError('Mac template contains compiled VBA performance cache')
        if storage.openstream('VBA/_VBA_PROJECT').read() != bytes.fromhex('CC61FFFF000300'):
            raise ValueError('Mac template must compile from portable source')
        directory = decompress(storage.openstream('VBA/dir').read())
        entries = list(records(directory))
        if entries[0] != (1,0,struct.pack('<I',2)):
            raise ValueError('VBA project is not marked for Macintosh')
        if any(kind in (0x0D,0x0E,0x2F,0x33) for kind,_,_ in entries):
            raise ValueError('Portable template must not depend on external Windows libraries')
        if next(value for kind,_,value in entries if kind==3) != struct.pack('<H',1252):
            raise ValueError('Unexpected portable VBA codepage')
        name = None; count = 0; sources = {}
        for kind,_,value in entries:
            if kind == 0x19:
                name = value.decode('ascii')
            elif kind == 0x31:
                if value != b'\0'*4 or not name:
                    raise ValueError('Mac module contains precompiled platform-dependent code')
                source = decompress(storage.openstream(['VBA',name]).read())
                source.decode('ascii')
                sources[name] = source
                count += 1
        if count != 2 or set(sources) != {'ThisDocument','BiomedReader'}:
            raise ValueError('Unexpected Mac VBA modules')
        if expected_source is not None and sources['BiomedReader'] != portable_source(expected_source):
            raise ValueError('Bundled VBA source and template do not match')
        return sources
