# source/uma_global.py
def scrape(search: dict, **kwargs) -> list[dict]:
    # placeholder: return ONE normalized record, hardcoded
    return [{
        "site_id": "uma_global",
        "trainer_id": "133102601857",
        "blue_list":   ["Stamina9 (Representative3)"],
        "pink_list":   ["Long6 (Representative2)"],
        "unique_list": ["Blue Rose Closer2 (Representative2)", "Flowery☆Maneuver2 (Representative2)"],
        "white_list":  ["Tail Held High2 (Representative2)", "Fighter1 (Representative1)"],
        "white_count": 15,
        "g1_count": 13,
        "id_url": "https://uma-global.pure-db.com/#/user/133102601857",
        "source_url": search["url"],
    }]
