NAME=douban
SRC=douban
ZIP=$(NAME).zip
SRC_FILES=__init__.py douban.py

$(ZIP): $(addprefix $(SRC)/,$(SRC_FILES))
	cd $(SRC) && zip -r ../$(ZIP) $(SRC_FILES)

install: $(ZIP) 
	calibre-customize -a $(ZIP)

test: install
	calibre-debug -e $(SRC)/douban.py

clean:
	rm -f $(ZIP)
