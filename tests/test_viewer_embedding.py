"""Integrity checks for portable offline hashes and exact binary deduplication."""
import base64
import hashlib
import json
import struct
import subprocess
import unittest
from pathlib import Path

from engine.build_viewer import shared_bin_parts

ROOT=Path(__file__).resolve().parents[1]


class OfflineIntegrityTests(unittest.TestCase):
    def test_portable_sha256_matches_authoritative_vectors_and_boundary_lengths(self):
        # Include FIPS common vectors and both sides of padding/block boundaries.
        values=[b"",b"abc",b"a"*1_000_000]+[bytes(range(256))[:n] for n in (55,56,63,64,65,127,128,129)]
        cases=[{"base64":base64.b64encode(raw).decode(),"expected":hashlib.sha256(raw).hexdigest()} for raw in values]
        script="import {sha256Portable} from "+json.dumps((ROOT/"engine/hash_sha256.mjs").as_uri())+";let text='';for await(const part of process.stdin)text+=part;for(const value of JSON.parse(text)){const actual=sha256Portable(Buffer.from(value.base64,'base64'));if(actual!==value.expected)throw Error('SHA-256 mismatch');}process.stdout.write('passed');"
        result=subprocess.run(["node","--input-type=module","-e",script],input=json.dumps(cases),text=True,capture_output=True,check=True)
        self.assertEqual(result.stdout,"passed")

    def test_shared_bin_reassembles_distinct_json_headers_byte_for_byte(self):
        binary=b"\x00\x01\x02\x03"*8
        chunk=struct.pack("<II",len(binary),0x004E4942)+binary
        originals=[]
        for material in ["stone","edited facade"]:
            document=json.dumps({"asset":{"version":"2.0"},"material":material}).encode()
            document+=b" "*(-len(document)%4)
            json_chunk=struct.pack("<II",len(document),0x4E4F534A)+document
            original=struct.pack("<4sII",b"glTF",2,12+len(json_chunk)+len(chunk))+json_chunk+chunk
            originals.append(original)
        pieces=[shared_bin_parts(raw) for raw in originals]
        self.assertEqual(pieces[0][2],pieces[1][2])
        for original,(header,json_chunk,_) in zip(originals,pieces):
            rebuilt=header+json_chunk+pieces[0][2]
            self.assertEqual(rebuilt,original)
            self.assertEqual(hashlib.sha256(rebuilt).digest(),hashlib.sha256(original).digest())
        self.assertIsNone(shared_bin_parts(originals[0][:-1]))


if __name__=="__main__":
    unittest.main()
