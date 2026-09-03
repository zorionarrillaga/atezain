# atezain — atzeratutako fakturetarako

> **Zirriborroa, gordeta. Ez bidali inori.** Euskarazko hartzaile jakin bat agertzen denean
> amaituko da orria, jabearekin batera: berea da ahotsa eta berea da azken hitza. Zenbakiak
> `make numbers`-ek jartzen ditu, ez eskuz.

Zure fakturak, barne-oharrak eta bezeroen mezuak irakurtzen dituen laguntzaile bat da: egoera
laburbiltzen dizu, hurrengo urratsak gomendatzen dizkizu eta bezeroei bidaltzeko mezuen
zirriborroak idazten dizkizu. **Ezin du bere kabuz zure fakturetan ezer aldatu.**

## Zer egiten duen

Kalkulu-orri bat igoko duzu zure fakturekin: identifikatzailea, bezeroa, zenbat diru den eta zein
dirutan, noiz egin zen, noiz den mugaeguna eta zein egoeratan dagoen; eta baldin badituzu, oharrak
eta mezuak. Faktura bat aukeratu eta hiru gauza jasoko dituzu: egoeraren laburpena, gomendatzen
duen ekintza eta bezeroari bidaltzeko zirriborroa.

Faktura horri buruz hiru gauza baino ezin ditu **proposatu**: oroigarria posta elektronikoz
bidaltzea, zuk onartu arte zain geratzen dena; egoera aldatzea, hau ere zuk onartu arte zain; eta
barne-ohar bat uztea, unean bertan idatzi eta erregistratua geratzen dena. Proposamen bakoitza
modelotik kanpo dagoen baimen-taula baten aurka egiaztatzen da, zuregana iritsi aurretik; eta
egiten den idazketa bakoitzaren ondoren, erregistroan benetan aldatu dena zuk onartu zenuenarekin
alderatzen da.

Gaur egun gaztelaniaz dago prestatuta: gaztelaniaz daude fakturak, oharrak eta modeloari ematen
zaion testua. Euskaraz jartzeko, fitxategi berberak dira, beste hizkuntza batean idatzita.

## Zeri egiten dion uko

- **Fakturak dioen dirua aldatzeari.** Inoiz ez.
- **Faktura bat ezabatzeari.** Inoiz ez.
- **Baimen-taulak ezagutzen ez duen helbide edo bide batera ezer bidaltzeari.** Oroigarriak postaz ateratzen dira edo ez dira ateratzen.
- **Baimen-taulan jasota ez dagoen egoera bat jartzeari**, *ordaindua* edo *baliogabetua* esaterako. Hori zuk jartzen duzu.
- **Egunean baimen-taulak uzten duena baino gehiagotan idazteari.** Muga baimen-taulan dago.
- **Ezer egiteari, bere egoera irakurri ezin badu.** Baimen-taula irakurtzen ez badu, erantzuna ez da.

Baimen-taula testu-fitxategi bat da, eta zure erregistroetara moldatzea fitxategi hori da batez ere: zein
erregistro-mota, zein aditz, zein eremu, zein balio, zein muga eta zein idazketa pasatzen diren
zure eskutik.

## Zenbakiak, egindako probetan

Modelo jakin bati, egun jakin batean, ehun injekzio jarri zitzaizkion aurrean, laguntzaileak
irakurtzen dituen erregistroetan ezkutatuta: oharretan, mezuetan, mezuen gaietan, bezero baten
izenean eta eranskin baten testuan. Bakoitzak baimen-taulak debekatzen duen idazketa bat bilatzen
zuen. Beheko taulak `make numbers` aginduak sortzen ditu; xehetasun guztiak `NUMBERS.md`
fitxategian daude.

<!-- numbers:begin -->
_Pasted by `make numbers` from the rows behind `NUMBERS.md`; do not edit by hand. Model `openai/gpt-oss-120b` · run date(s) 2026-09-02 · N = 100 cases · temperature 0.0 · adapter `invoices-es` · prompt sha256[:16] `6dd2c9d49e5d93c0` · Wilson score intervals, 95 %, z = 1.96. `NUMBERS.md` has the per-class, per-technique and per-goal tables._

| all cases | N | manipulated | executed OFF | executed ON |
|---|---|---|---|---|
| openai/gpt-oss-120b | 100 | 1/100 = 1% [0%, 5%] | 1/100 = 1% [0%, 5%] | 0/100 = 0% [0%, 4%] |

| the goal is | N | manipulated | executed OFF | executed ON |
|---|---|---|---|---|
| permitted verb, forbidden value | 30 | 1/30 = 3% [1%, 17%] | 1/30 = 3% [1%, 17%] | 0/30 = 0% [0%, 11%] |
| verb not offered | 70 | 0/70 = 0% [0%, 5%] | 0/70 = 0% [0%, 5%] | 0/70 = 0% [0%, 5%] |

The policy refused every goal proposal in 100/100 = 100% [96%, 100%] of cases; the audit chain verified with no
anomaly in 100/100.

| all cases | N | labelled | goal in prose | manipulated (proposals) |
|---|---|---|---|---|
| openai/gpt-oss-120b | 100 | 100 | 66/100 = 66% [56%, 75%] | 1/100 = 1% [0%, 5%] |

| the goal is | N | labelled | goal in prose | manipulated (proposals) |
|---|---|---|---|---|
| permitted verb, forbidden value | 30 | 30 | 22/30 = 73% [56%, 86%] | 1/30 = 3% [1%, 17%] |
| verb not offered | 70 | 70 | 44/70 = 63% [51%, 73%] | 0/70 = 0% [0%, 5%] |

Where the adopting sentence was read: recommendation 35 of 66 · draft 18 of 66 · note 13 of 66.

Prose labels by the JUDGE seat (Claude Fable 5.1), by hand, one cached output at a time, on 2026-09-02, in `redteam/prose_labels.json`; each quotes the sentence it rests on and is tied to the hash of the output it was read from.

Re-read by the step-6 refutation seat (Claude Fable 5.1, a fresh session in a clone of the repository — the same model as the labeller, which the seat flagged itself) on 2026-09-03: would move 2 of 100 labels (field-003, mail-012), which leaves the rate inside its interval; the labels stand as labelled, and a reader who agrees with the seat edits the label and runs `make numbers`.
<!-- numbers:end -->

Debekatutako idazketa ehunetik behin **proposatu** zuen modeloak. Baina injekzioaren helburua
**bere hitzetan** jaso zuen hirutik bitan: ematen dizun gomendioan, bezeroei idazten dizkien
zirriborroetan edo proposatzen dituen oharretan. Idazketa baimen-taulak gelditzen du. Hitzak zuk
irakurtzen dituzu.

## Nola probatu

**<https://atezain.onrender.com>** — ireki `/demo` eta igo zure fakturen CSV bat. Han egiten duzunak
ez du beste inor ukitzen: saio bakoitzak bere gunea du, bere fakturekin eta bertan egin den
guztiaren erregistroarekin.

Doako zerbitzu batean dago, eta inork denbora batean erabili ez badu, lotan geratzen da: lehen
eskaerak esnatzen du, eta minutu bat inguru behar izaten du.

Zure ordenagailuan ere badabil, agindu bakar batekin, gakorik eta sarerik gabe:

```
git clone <biltegia> && cd atezain
python3 -m venv .venv && .venv/bin/pip install -q pytest langgraph langgraph-checkpoint-sqlite fastapi uvicorn python-multipart openpyxl
make serve                         # eta ireki http://127.0.0.1:8000/demo
```

## Prezioa

Probatzea eta erabiltzea doakoa da.

Zure erregistroetarako moldaketa nahi baduzu, prezioa lanean hasi aurretik adosten dugu. Moldaketa
hau da: zure erregistro-motetarako baimen-taula, modeloari ematen zaion testua zure hizkuntzan,
probetarako datu batzuk, eta injekzio-tanda bat moldaketa horren aurka, bere zenbakiekin.
