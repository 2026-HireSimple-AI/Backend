import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs

def clean(text):
    return text.replace("\xa0", "").strip()

def parse_summary(soup):
    result = {}
    for dl in soup.select("div.cont dl"):
        dt = dl.find("dt")
        dd = dl.find("dd")
        if not dt or not dd:
            continue

        key = clean(dt.get_text())
        if key == "근무조건":
            break

        tooltip_wrap = dd.select_one(".toolTipWrap")
        details = []
        if tooltip_wrap:
            for li in tooltip_wrap.select(".toolTipCont li"):
                label_tag = li.find("span")
                if label_tag:
                    label = clean(label_tag.get_text())
                    label_tag.extract()
                    value = clean(li.get_text())
                    details.append({label: value})
                else:
                    details.append(clean(li.get_text()))
            tooltip_wrap.extract()

        for btn in dd.select("button"):
            btn.extract()

        main_text = clean(dd.get_text(" "))
        result[key] = details if details else main_text

    return result

def scrape_job_posting(url):
    rec_idx = parse_qs(urlparse(url).query)["rec_idx"][0]

    session = requests.Session()
    session.headers.update({
        "accept": "text/html, */*; q=0.01",
        "accept-language": "ko",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
        "x-requested-with": "XMLHttpRequest",
    })

    result = {
        "title": None,
        "input_type": "url",
        "source_url": url,
        "raw_content": None,
        "conts_summary": None
        }

    # ---- 1번째 요청: view-ajax (POST) → 요약 정보 테이블 ----
    ajax_url = "https://www.saramin.co.kr/zf_user/jobs/relay/view-ajax"
    ajax_payload = {
        "rec_idx": rec_idx,
        "rec_seq": "0",
        "view_type": "list",
        "t_ref": "",
        "t_ref_content": "",
        "ref_dp": "SRI_050_VIEW_MIX_RCT_NONMEM",
    }

    try:
        res = session.post(
            ajax_url,
            data=ajax_payload,
            headers={
                "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                "origin": "https://www.saramin.co.kr",
                "referer": url,
            },
        )
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        title = soup.select("h1.tit_job")[0].text.strip()
        result["title"] = title
        # cont = soup.select_one("div.cont")

        # if cont:
        #     lines = [l.strip() for l in cont.text.split("\n") if l.strip()]
        #     detail = {}
        #     for i in range(1, len(lines), 2):
        #         detail[lines[i - 1]] = lines[i]
        #         if lines[i - 1] == "근무형태":
        #             break
        #     result["conts_summary"] = lines

        details = parse_summary(soup)
        result["conts_summary"] = details

    except Exception as e:
        print(f"[view-ajax 실패] {rec_idx}: {e}")

    # ---- 2번째 요청: view-detail (GET) → 실제 본문 ----
    detail_url = (
        "https://www.saramin.co.kr/zf_user/jobs/relay/view-detail"
        f"?rec_idx={rec_idx}&rec_seq=0"
        "&t_category=non-logged_relay_view&t_content=view_detail"
        "&t_ref=&t_ref_content="
    )

    try:
        res = session.get(detail_url, headers={"referer": url})
        res.raise_for_status()
        res.encoding = res.apparent_encoding
        detail_soup = BeautifulSoup(res.text, "html.parser")

        contents = detail_soup.select_one("div.user_content")
        text1 = contents.select_one("div.job-content") if contents else None
        text2 = contents.select_one("div.content") if contents else None
        text3_locator = detail_soup.select_one("body > div > div > div:nth-child(2)")
        text3_text = text3_locator.get_text(strip=True) if text3_locator else ""

        if text1:
            data = text1.get_text(strip=True).replace("\xa0", " ")
        elif text2:
            data = text2.get_text(strip=True).replace("\xa0", " ")
        elif text3_text:
            data = text3_text.replace("\xa0", " ")
        else:
            images = contents.select("img") if contents else []
            data = []
            for img in images:
                src = img.get("src")
                if not src:
                    continue
                if src.startswith("//"):
                    src = "https:" + src
                data.append(src)
        result["raw_content"] = data

    except Exception as e:
        print(f"[view-detail 실패] {rec_idx}: {e}")

    return result