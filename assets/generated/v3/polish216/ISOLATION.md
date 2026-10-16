This directory carries the SOU-216 polish pass. It lives OUTSIDE the home sets' generated
trees (`assets/generated/v3/lodge|serverroom|writer`) on purpose: those trees are cleaned
and reassembled by their own modules (`gen_lodge_art.py clean`, `gen_roomart_b.py clean`),
and a delivered polish file must not be deletable by another module. The SERVED files land
in their home set's directory, which is where the wiring step reads them.
