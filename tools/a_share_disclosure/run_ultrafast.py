#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import time

import export_disclosures_ultrafast as exporter

exporter.APPOINT_API = "http://www.cninfo.com.cn/new/information/getPrbookInfo"


def request_json_fast(url, *, params=None, data=None, retries=2, sleep=0.35):
    last = None
    for attempt in range(1, retries + 1):
        try:
            response = exporter.SESSION.post(url, params=params, data=data, timeout=15)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last = exc
            if attempt < retries:
                time.sleep(sleep * attempt)
    raise RuntimeError(str(last))


exporter.request_json = request_json_fast

if __name__ == "__main__":
    exporter.main()
