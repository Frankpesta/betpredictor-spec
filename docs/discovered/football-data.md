# football-data.co.uk — discovered facts

Verified: **2026-09-27** (Phase 1 discovery). Raw copies:
`data/raw/football_data/2026-09-27/{notes.txt, E0_2627.csv, E0_1920.csv, SP1_2627.csv}`.

## Host / URL
- `https://www.football-data.co.uk/...` answers **302** → `https://football-data.co.uk/...`
  (bare domain). The adapter uses the bare domain directly:
  `https://football-data.co.uk/mmz4281/{SSSS}/{CODE}.csv` (e.g. `2627/E0.csv`).
- Response headers include `X-WS-RateLimit-Limit: 1000` — we stay far below it
  (one request per 4–9 s, single worker).
- Content-Type `text/csv`.

## Encoding
- Current season (`2627/E0.csv`, `2627/SP1.csv`): **UTF-8 with BOM** (`EF BB BF`).
- Older season (`1920/E0.csv`): plain ASCII, no BOM.
- Parse: try `utf-8-sig`, fall back to `latin-1`.

## Header rows (verbatim)

`2627/E0.csv` (114 columns, 50 rows on 2026-09-27):
```
Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HTHG,HTAG,HTR,Referee,HxG,AxG,HS,AS,HST,AST,HF,AF,HC,AC,HY,AY,HR,AR,B365H,B365D,B365A,BFDH,BFDD,BFDA,BVH,BVD,BVA,BWH,BWD,BWA,PPH,PPD,PPA,SKBH,SKBD,SKBA,MaxH,MaxD,MaxA,AvgH,AvgD,AvgA,BFEH,BFED,BFEA,B365>2.5,B365<2.5,Max>2.5,Max<2.5,Avg>2.5,Avg<2.5,BFE>2.5,BFE<2.5,AHh,B365AHH,B365AHA,MaxAHH,MaxAHA,AvgAHH,AvgAHA,BFEAHH,BFEAHA,B365CH,B365CD,B365CA,BFDCH,BFDCD,BFDCA,BVCH,BVCD,BVCA,BWCH,BWCD,BWCA,PPCH,PPCD,PPCA,SKBCH,SKBCD,SKBCA,MaxCH,MaxCD,MaxCA,AvgCH,AvgCD,AvgCA,BFECH,BFECD,BFECA,B365C>2.5,B365C<2.5,MaxC>2.5,MaxC<2.5,AvgC>2.5,AvgC<2.5,BFEC>2.5,BFEC<2.5,AHCh,B365CAHH,B365CAHA,MaxCAHH,MaxCAHA,AvgCAHH,AvgCAHA,BFECAHH,BFECAHA
```

`1920/E0.csv` (106 columns, 380 rows):
```
Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HTHG,HTAG,HTR,Referee,HS,AS,HST,AST,HF,AF,HC,AC,HY,AY,HR,AR,B365H,B365D,B365A,BWH,BWD,BWA,IWH,IWD,IWA,PSH,PSD,PSA,WHH,WHD,WHA,VCH,VCD,VCA,MaxH,MaxD,MaxA,AvgH,AvgD,AvgA,B365>2.5,B365<2.5,P>2.5,P<2.5,Max>2.5,Max<2.5,Avg>2.5,Avg<2.5,AHh,B365AHH,B365AHA,PAHH,PAHA,MaxAHH,MaxAHA,AvgAHH,AvgAHA,B365CH,B365CD,B365CA,BWCH,BWCD,BWCA,IWCH,IWCD,IWCA,PSCH,PSCD,PSCA,WHCH,WHCD,WHCA,VCCH,VCCD,VCCA,MaxCH,MaxCD,MaxCA,AvgCH,AvgCD,AvgCA,B365C>2.5,B365C<2.5,PC>2.5,PC<2.5,MaxC>2.5,MaxC<2.5,AvgC>2.5,AvgC<2.5,AHCh,B365CAHH,B365CAHA,PCAHH,PCAHA,MaxCAHH,MaxCAHA,AvgCAHH,AvgCAHA
```

## docs/02 §1.3 columns — presence

| column group | 1920/E0 | 2627/E0 |
|---|---|---|
| `Div Date Time HomeTeam AwayTeam FTHG FTAG` | all | all |
| `B365>2.5 B365<2.5 Avg>2.5 Avg<2.5` (+ `Max…`) | yes | yes |
| `P>2.5 P<2.5` (Pinnacle O/U open) | yes | **absent** |
| `B365C>2.5 … AvgC<2.5` (+ `MaxC…`) | yes | yes |
| `PC>2.5 PC<2.5` (Pinnacle O/U close) | yes | **absent** |
| `AHh B365AHH B365AHA AvgAHH AvgAHA` (+ `Max…`) | yes | yes |
| `PAHH PAHA` | yes | **absent** |
| `AHCh B365CAHH … AvgCAHA` (+ `MaxC…`) | yes | yes |
| `PCAHH PCAHA` | yes | **absent** |
| 1X2: `B365H/D/A`, `PSH/D/A`, `AvgH/D/A`, `MaxH/D/A` (+ `C` closing) | yes | Pinnacle absent |

All spellings match docs/02 §1.3 exactly, so `FD_COLUMN_MAP` needs no renames.
**Pinnacle columns are missing from the 2026-27 file.** Per-season presence for
every ingested file is in the table at the bottom (filled from the full ingest).

New since docs were written (not used in v1): `HxG`/`AxG` (xG in the CSV
itself), `BFE*` (Betfair Exchange), `BFD*`, `BV*`, `PP*`, `SKB*`.

## Dates and times
- `Date` in both files is `dd/mm/yyyy` (10 chars, every row). `notes.txt` still
  says `dd/mm/yy`; the parser accepts both.
- `Time` present on every row of both files.
- **Timezone: `notes.txt` does not state it.** Evidence it is UK local time
  (`Europe/London`) for all leagues:
  - `2627/SP1.csv` kickoff times are 15:15, 16:00, 17:30, 18:00, 18:30, 19:30,
    20:00 (11×), 20:30 (8×) — never 21:00/21:30. La Liga's usual local (CEST)
    slots are 16:15/18:30/21:00/21:30, i.e. the file is one hour behind Spain = UK.
  - Cross-check against Understat (which reports UTC, see `understat.md`):
    after the full ingest, kickoff differences between
    football-data (UK→UTC) and Understat are counted per league; see bottom table.

## Asian handicap sign convention (checked by eye)
`AHh` is the handicap applied to the **home** team; negative = home favoured:

| match | B365H | B365A | AHh |
|---|---|---|---|
| 2627 Arsenal v Coventry | 1.20 | 13 | −2 |
| 2627 Hull v Man United | 8.5 | 1.36 | +1.5 |
| 2627 Everton v Crystal Palace | 2.15 | 3.3 | −0.25 |
| 1920 Liverpool v Norwich | 1.14 | 19 | −2.25 |
| 1920 West Ham v Man City | 12 | 1.22 | +1.75 |

Opening (`AHh`) and closing (`AHCh`) lines can differ on the same row
(2627 Brentford v Tottenham: `AHh` 0, `AHCh` −0.5), so each timing stores its own line.

## Empty rows
No fully empty rows in these three files; parser still counts/logs any it drops.

## Sample rows (trimmed to identity + a few odds)
```
E0,21/08/2026,20:00,Arsenal,Coventry,3,0,...,B365>2.5=1.57,B365<2.5=2.38,AHh=-2,B365AHH=2.03,B365AHA=1.78
E0,09/08/2019,20:00,Liverpool,Norwich,4,1,...,B365>2.5=1.4,B365<2.5=3,P>2.5=1.4,P<2.5=3.11,AHh=-2.25
```

## Per-file results from the full ingest (2026-09-27)

40 files (5 leagues × 2019-20..2026-27), 12,709 matches, 397,413 odds rows,
0 empty rows, 0 skipped rows, 0 rows without `Time`, all dates `dd/mm/yyyy`.
Ligue 1 2019-20 has 279 matches (season curtailed by COVID — genuine).
Ligue 1 has 306 matches from 2023-24 (18 teams).

Every §1.3 column is present in every file **except Pinnacle**:

| seasons | `P>2.5 P<2.5 PC>2.5 PC<2.5 PAHH PAHA PCAHH PCAHA` |
|---|---|
| 2019-20 .. 2024-25 | present and filled |
| 2025-26 | columns present, but **values stop in mid-January 2026** (last Pinnacle row per league: EPL 2026-01-08, LIGUE1 01-04, LALIGA 01-12, BUNDES 01-15, SERIEA 01-15). 899 of 1,752 matches have Pinnacle odds. |
| 2026-27 | columns **absent** |

Impact (for Phase 2, not Phase 1): `backtest.closing_source = "PS"` has no data after
mid-January 2026, and 2025-26 is a `test_season`.

## Kickoff timezone cross-check vs Understat (all 12,708 joined matches)
football-data `Date`+`Time` read as `Europe/London` then converted to UTC matches
Understat's time **exactly for 95.2%** of matches. Of the rest:
- 369 differ by exactly −60 min, clustered on whole matchdays in Aug–Oct 2019
  and on the spring DST-change Sundays (2024-03-31, 2025-03-30). Checked by hand:
  Liverpool v Norwich 2019-08-09 kicked off 20:00 BST = 19:00 UTC; football-data
  gives 19:00 UTC, Understat says `20:00` — so the error is on **Understat's** side
  (see `understat.md`).
- the remainder are ±15–45 min or <1 day apart (rescheduled kickoffs / data-entry
  differences); a handful (6) are ≥ 1 day apart (rearranged fixtures).
Conclusion: the UK-local assumption is correct; kickoff times come from football-data only.
