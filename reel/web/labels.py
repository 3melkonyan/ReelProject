"""Single Armenian source for dashboard stage names; stage identifiers stay stable."""
STAGES = [
    ("join", "Մասերի միացում"), ("probe", "Ֆայլի ստուգում"),
    ("audio", "Ձայնի առանձնացում"), ("transcribe", "Խոսքի վերծանում"),
    ("segments", "Կտրման պլան"), ("render", "Մոնտաժ և ձայն"),
    ("blur", "Դեմքերի մշուշում"), ("privacy", "Գաղտնիության ստուգում"),
    ("preview", "Վեբ դիտման տարբերակ"), ("metadata", "Վերնագիր և գլուխներ"),
    ("summary", "Ամփոփում և հարցեր"),
    ("thumbnail", "Շապիկ"), ("youtube_upload", "Տեսանյութի վերբեռնում"),
    ("playlist", "Ավելացում նվագացանկում"), ("thumbnail_set", "Շապիկի տեղադրում"),
    ("captions", "Հայերեն ենթագրեր"),
    ("slack_post", "Ծանուցում խմբին"), ("classroom_post", "Հրապարակում դասարանում"),
    ("library_add", "Ավելացում գրադարանում"),
]
STAGE_LABEL = dict(STAGES)
