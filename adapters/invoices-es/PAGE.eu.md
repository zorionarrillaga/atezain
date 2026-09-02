# atezain — atzeratutako fakturetarako

> **Zirriburua. Euskarazko testu hau ez du oraindik jabeak irakurri**, eta bere ahotsa da azkena.
> Ez bidali inori berak berrikusi arte. Zenbakiak `make numbers`-ek jartzen ditu, ez eskuz.

*Atezain* hitzak esaten du tresnak egiten duena: gelditu. Zure fakturak, barne-oharrak eta
bezeroen mezuak irakurtzen dituen laguntzaile bat da. Egoera laburbiltzen du, hurrengo urratsa
gomendatzen du eta mezua idazten dizu — eta **ezin du berak ezer aldatu**.

## Zer egiten duen

Kalkulu-orri bat igotzen duzu zure fakturekin: identifikatzailea, bezeroa, zenbatekoa, moneta,
jaulkipen-data, mugaeguna eta egoera; eta baldin badituzu, oharrak eta mezuak. Faktura bat
aukeratzen duzu eta hiru gauza jasotzen dituzu: egoeraren laburpena, gomendatzen duen ekintza eta
bezeroari bidaltzeko zirriborroa.

Faktura horri buruz hiru gauza baino ezin ditu **proposatu**: oroigarria posta elektronikoz
bidaltzea, zure oniritziaren zain geratzen dena; egoera aldatzea, hori ere zure oniritziaren zain;
eta barne-ohar bat uztea, unean bertan idazten dena eta erregistratuta geratzen dena. Proposamen
bakoitza modelotik kanpo dagoen baimen-taula baten aurka egiaztatzen da, zuregana iritsi aurretik
ere; eta gertatzen den idazketa bakoitzaren ondoren, erregistroan benetan aldatu dena zuk onartu
zenuenarekin alderatzen da.

Gaur egungo egokitzapena gaztelaniaz lan egiten duen bat da: hor daude fakturak, oharrak eta
prompta. Euskaraz lan egiten duen bat fitxategi berberak dira, beste hizkuntza batean.

## Zeri egiten dion uko

- **Zenbateko bat aldatzeari.** Inoiz ez, nahiz eta bezeroaren mezu batek, lankide baten ohar batek
  edo laguntzailearen beraren arrazoibideak hala eskatu. Zenbateko bat dirua da.
- **Faktura bat ezabatzeari.** Inoiz ez.
- **Taulak ezagutzen ez duen helbide edo bide batera ezer bidaltzeari.** Oroigarriak postaz
  ateratzen dira edo ez dira ateratzen. «Hobe WhatsApp honetara bidali» dioen ohar bat oharra idatzi
  zuenari buruzko informazioa da, ez zuretzako agindua.
- **Taulan jasota ez dagoen egoera bat jartzeari**, *ordaindua* edo *baliogabetua* esaterako. Hori
  zuk jartzen duzu bankuko laburpenetik, ez laguntzaileak mezu batetik.
- **Egunean taulak uzten duena baino gehiagotan idazteari.** Muga taulan dago, eta hura jotzean
  fusible bat saltatzen da: laguntzaileak ezin du berrarmatu, eta inork ez du egun berean berrarmatzen.
- **Ezer egiteari, bere egoera irakurri ezin badu.** Baimenen datu-basea eskuratzen ez badu,
  erantzuna ez da, inoiz ez baiezkoa.

Taula testu-fitxategi bat da, eta zure erregistroetara egokitzea fitxategi hori da batez ere: zein
erregistro-mota, zein aditz, zein eremu, zein balio, zein muga eta zein idazketa pasatzen diren
zure eskutik.

## Zenbakiak, bere proba-multzoaren gainean

Izena eta data duen modelo bati ehun injekzio aurkeztu zitzaizkion, laguntzaileak irakurtzen dituen
erregistroetan landatuta — oharretan, mezuetan, gaietan, bezero baten izenean, eranskin baten
testuan — bakoitza taulak debekatzen duen idazketa bat bilatzen. Beheko taula `make numbers`-ek
sortzen du; xehetasun guztiak `NUMBERS.md` fitxategian daude.

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

Where the adopting sentence was read: recommendation 35 · draft 18 · note 13.

Prose labels by the JUDGE seat (Claude Fable 5.1), by hand, one cached output at a time, on 2026-09-02, in `redteam/prose_labels.json`; each quotes the sentence it rests on and is tied to the hash of the output it was read from.
<!-- numbers:end -->

Azken taula lehen biak bezain garrantzitsua da. Modeloak ehunetik behin **proposatu** zuen debekatutako
idazketa. Baina injekzioaren helburua **bere hitzetan** hartu zuen — ematen dizun gomendioan, zure
bezeroari idazten dion zirriborroan edo proposatzen duen ohar batean — hirutik bitan. Geruzak
idazketa gelditzen du. Hitzak zuk irakurtzen dituzu, eta hemen inork ez ditu zure ordez irakurtzen:
hori da, hain zuzen, orri honek agintzen ez dizuna.

## Nola probatu

**<https://atezain.onrender.com>** — ireki `/demo` eta igo zure fakturen CSV bat. Han egiten duzunak
ez du beste inor ukitzen: saio bakoitza bere gunea da, bere erregistroekin eta bere auditoria-katearekin.

Doako instantzia bat da, beraz inork denbora batean erabili ez badu, lehen eskaerak esnatu egiten du
eta minutu bat inguru behar du.

Ordenagailu batean ere badabil, agindu bakar batekin, gakorik eta sarerik gabe:

```
git clone <biltegia> && cd atezain
python3 -m venv .venv && .venv/bin/pip install -q pytest langgraph langgraph-checkpoint-sqlite fastapi uvicorn python-multipart openpyxl
make serve                         # eta ireki http://127.0.0.1:8000/demo
```

## Zenbat balio duen

Probatzeak ez du ezer balio, eta oraingoz ez dago preziorik. Lehenengo behar dena da norbaitek bere
fakturen gainean erabiltzea eta esatea balio dion; horrek gehiago balio du gaur, kobra litekeenak baino.

Gero zure erregistroetarako egokitzapena nahi baduzu, orduan hitz egiten da, dagoeneko badakizunean
balio dizun ala ez. Zertan datzan goian esanda dago: zure erregistro-motetarako baimen-taula, prompta
zure hizkuntzan eta zure erregistroan, zureak ordezkatuko dituzten datu batzuk probatzen den bitartean,
eta injekzio-tanda bat egokitzapen horren aurka, bere zenbakiekin, zuk hartaz fidatu aurretik.
