# Pointing sounds

From the earshot bank (github.com/Leonxlnx/earshot, MIT; the bank is synthesised in code, no recordings):

| file | earshot id | used for | level |
|---|---|---|---|
| ui-ring.wav | pro_notify/ding_2045 | a circle drawing on | peak = the voice's peaks (`pk: 0`) |
| ui-pop.wav | pop/2 | a label appearing | 1 dB under (`pk: -1`) |
| ui-arrow.wav | pro_zip/arc | an arrow drawing on | peak = the voice's peaks (`pk: 0`); swish/flick was masked by the stump-hit tail (0 dB), zip/arc 12.1 dB |

Chosen 2026-11-13 with earshot against the owner's reference Short P4e7XbHtux4 (Textuju, 1.16M views), whose pops and dings
stand a median 14.2 dB over their bed. Each candidate was mixed into 028's audio at its 11 pointing moments and measured
(median prominence): ding_2045 10.8, ding_1930 10.3, pop/2 8.5, celesta 7.6, drip/plink 7.4, pro_pop_in/round 7.0, glock 5.9, bloop 5.7.
Soft, rounded sounds are masked by our dense narration; bright, sharp ones cut through.
Re-render with: earshot sfx render <id> -o <file>
