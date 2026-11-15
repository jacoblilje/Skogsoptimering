# Skogsoptimering

API och optimeringsmodell bakom Skogsplan: en flerårig skatteplan för enskild
näringsverksamhet med skog. Modellen väljer avverkningsår, skogskonto,
skogsavdrag, räntefördelning, periodiseringsfond och expansionsfond så att
nuvärdet av det ägaren får behålla efter skatt blir så högt som möjligt.

## Filer

| Fil | Innehåll |
|---|---|
| `forest_lp_realworld.py` | Optimeringsmodellen (blandad heltalsprogrammering, PuLP + HiGHS) |
| `tax_curve.py` | Marginalskatt, kommunalskatter och belopp per inkomstår |
| `declaration_parser.py` | Läser förra årets NE-bilaga (PDF) och föreslår indata |
| `api_server.py` | FastAPI: `/solve`, `/parse-declaration`, `/tax-schedule`, `/kommuner`, `/year-defaults/{år}` |
| `tests/` | Tester. `tests/audit.py` räknar om varje plan oberoende av modellen |

## Köra

```bash
pip install -r requirements.txt
python tests/run.py                       # alla tester (pytest fungerar också)
uvicorn api_server:app --port 8000
```

## Regler som modellen följer

Beräkningen går i samma ordning som NE-bilagan (SKV 2161), ruta R24–R48.

| Regel | I modellen |
|---|---|
| Skogskonto | Insättning högst 60 % av avverkningsrätt och 40 % av leveransvirke; uttag senast 10 år efter insättning; insättningen får inte ge underskott. Ränta står kvar på kontot och beskattas vid uttag (gäller från 1 april 2026). |
| Skogsskadekonto | 80 % / 50 % och 20 år. |
| Skogsavdrag | Högst 50 % / 30 % av årets intäkt (100 % / 60 % under rationaliseringsförvärvets år), totalt högst avdragsutrymmet. |
| Räntefördelning | Fördelningsbelopp = statslåneräntan + 6 procentenheter gånger kapitalunderlaget vid årets ingång. Avdraget får inte överstiga resultatet före räntefördelning (R29). Outnyttjat belopp sparas och höjer nästa års underlag. Beskattas med 30 %. |
| Kapitalunderlag | Eget kapital + sparat fördelningsbelopp + underskott − periodiseringsfonder − 79,4 % av expansionsfonden + 50 % av skogskontot. |
| Negativ räntefördelning | Tvingande när underlaget är lägre än −500 000 kr (från 2025): statslåneräntan + 1 procentenhet på hela underlaget. |
| Periodiseringsfond | Avsättning högst 30 % av resultatet efter räntefördelning (R33); återförs senast sjätte året. |
| Expansionsfond | Avsättning högst resultatet före avsättning (R35); 20,6 % expansionsfondsskatt som återfås vid återföring; fonden högst 125,94 % av kapitalunderlaget vid årets utgång. |
| Underskott | Rullas till nästa år (R48 → R24). |
| Marginalskatt | (avgift + kommunalskatt [+ 20 % statlig]) / (1 + avgift), där avgiften är 28,97 % (aktiv), 24,26 % (passiv) eller 10,21 % (fyllt 67). Brytpunkten räknas om till överskott före egenavgifter. |

Belopp per inkomstår finns i `tax_curve.py` (`_YEAR_DEFAULTS`) och ska
uppdateras varje år när statslåneräntan den 30 november är fastställd.

## Kända förenklingar

- **Eget kapital är konstant** över planen. Vinster som stannar i verksamheten
  höjer i verkligheten kapitalunderlaget.
- **Slutvärde.** Pengar som står kvar på skogskonto, i fonder eller hos
  virkesköparen vid planens slut värderas som om de beskattas med den lägre
  marginalskatten året efter planen.
- **Jobbskatteavdrag, grundavdragets avtrappning och nedsättningen av
  egenavgifter** (högst 15 000 kr per år) ingår inte i marginalskatten.
- **Minimibelopp** (5 000 kr skogskonto, 50 000 kr skogsskadekonto, 15 000 kr
  skogsavdrag) hindrar inte planen, men flaggas i resultatet.
- **Skogsskadekonto** förutsätter att villkoren är uppfyllda (mer än en
  tredjedel av skogen, minst 75 % av årets skogsintäkt) och gäller då hela
  planen.
- Naturvårdskonto, ersättningsfonder, återföring av skogsavdrag vid
  försäljning, övriga inkomster (lön, pension) och medhjälpande make ingår inte.
- Brytpunkten för den som fyllt 67 är kontrollerad för 2025 och 2026; övriga
  år är ungefärliga.
- Om periodiseringsfonder ska minska kapitalunderlaget för räntefördelning
  följer modellen uppställningen i deklarationsprogrammen. **Stäm av med
  revisor**; primärkällan (33 kap. inkomstskattelagen) har inte kontrollerats
  för den punkten.

## Inläsning av deklaration

`POST /parse-declaration` tar en PDF som rå request-body. Filen läses i
minnet och sparas eller loggas aldrig; svaret innehåller bara belopp ur
namngivna rutor. Läsningen är regelbaserad (rutkoder och rubriker i PDF:ens
textlager), så:

- skannade deklarationer utan textlager kan inte läsas,
- läsaren är hittills bara testad mot syntetiska NE-bilagor
  (`tests/test_declaration.py`) – **inte mot en riktig deklaration**,
- äldre skogskontosaldon, äldre periodiseringsfonder och expansionsfondens
  saldo finns inte i NE-bilagan och måste fyllas i för hand.
