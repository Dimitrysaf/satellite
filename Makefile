all: buildmo

buildmo:
	@echo "Building the mo files"
	for file in `ls po/*.po 2>/dev/null`; do \
		lang=`echo $$file | sed 's@po/@@' | sed 's/\.po//' | sed 's/satellite-//'`; \
		install -d usr/share/locale/$$lang/LC_MESSAGES/; \
		msgfmt -o usr/share/locale/$$lang/LC_MESSAGES/satellite.mo $$file; \
	done \

clean:
	rm -rf usr/share/locale
