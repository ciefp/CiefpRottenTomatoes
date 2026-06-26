#!/bin/bash
##setup command=wget -q "--no-check-certificate" https://raw.githubusercontent.com/ciefp/CiefpRottenTomatoes/main/installer.sh -O - | /bin/sh

######### Only This 2 lines to edit with new version ######
version='1.3'
changelog='\nAdded Trailer Player\nAdded Auto EPG Search\nFixed Editorial parsing\nAdded yt-dlp support\nImproved search and parsing'
##############################################################

# Check if we should skip restart (for batch installations)
SKIP_REBOOT="${SKIP_REBOOT:-0}"

TMPPATH=/tmp/CiefpRottenTomatoes

if [ ! -d /usr/lib64 ]; then
	PLUGINPATH=/usr/lib/enigma2/python/Plugins/Extensions/CiefpRottenTomatoes
else
	PLUGINPATH=/usr/lib64/enigma2/python/Plugins/Extensions/CiefpRottenTomatoes
fi

# check depends packges
if [ -f /var/lib/dpkg/status ]; then
   STATUS=/var/lib/dpkg/status
   OSTYPE=DreamOs
else
   STATUS=/var/lib/opkg/status
   OSTYPE=Dream
fi
echo ""
if python --version 2>&1 | grep -q '^Python 3\.'; then
	echo "You have Python3 image"
	PYTHON=PY3
	Packagesix=python3-six
	Packagerequests=python3-requests
	Packageytdlp=python3-yt-dlp
else
	echo "You have Python2 image"
	PYTHON=PY2
	Packagerequests=python-requests
	Packageytdlp=yt-dlp  # Python2 koristi yt-dlp bez python3 prefixa
fi

# Install python3-six (only for Python3)
if [ $PYTHON = "PY3" ]; then
	if grep -qs "Package: $Packagesix" $STATUS ; then
		echo "python3-six already installed"
	else
		echo "Installing python3-six..."
		opkg update && opkg install python3-six
	fi
fi
echo ""

# Install python-requests
if grep -qs "Package: $Packagerequests" $STATUS ; then
	echo "$Packagerequests already installed"
else
	echo "Need to install $Packagerequests"
	echo ""
	if [ $OSTYPE = "DreamOs" ]; then
		apt-get update && apt-get install python-requests -y
	elif [ $PYTHON = "PY3" ]; then
		opkg update && opkg install python3-requests
	elif [ $PYTHON = "PY2" ]; then
		opkg update && opkg install python-requests
	fi
fi
echo ""

# Install yt-dlp (for YouTube trailers)
echo "Checking yt-dlp installation..."
if command -v yt-dlp >/dev/null 2>&1; then
	echo "yt-dlp already installed"
else
	echo "Installing yt-dlp..."
	if [ $OSTYPE = "DreamOs" ]; then
		apt-get update && apt-get install yt-dlp -y
	elif [ $PYTHON = "PY3" ]; then
		# Pokušaj prvo iz repozitorija
		if opkg list | grep -q "$Packageytdlp"; then
			opkg update && opkg install $Packageytdlp
		else
			# Ako nema u repozitoriju, instaliraj ručno
			echo "yt-dlp not in repository, installing manually..."
			wget -q https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -O /usr/bin/yt-dlp
			chmod 755 /usr/bin/yt-dlp
		fi
	elif [ $PYTHON = "PY2" ]; then
		# Za Python2 pokušaj instalirati yt-dlp
		if opkg list | grep -q "$Packageytdlp"; then
			opkg update && opkg install $Packageytdlp
		else
			echo "yt-dlp not in repository, installing manually..."
			wget -q https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -O /usr/bin/yt-dlp
			chmod 755 /usr/bin/yt-dlp
		fi
	fi
fi

# Provjeri da li je yt-dlp uspješno instaliran
if command -v yt-dlp >/dev/null 2>&1; then
	echo "✅ yt-dlp installed successfully"
else
	echo "⚠️ yt-dlp installation failed - YouTube trailers may not work"
fi
echo ""

## Remove tmp directory
[ -r $TMPPATH ] && rm -f $TMPPATH > /dev/null 2>&1

## Remove old plugin directory
[ -r $PLUGINPATH ] && rm -rf $PLUGINPATH

# Download and install plugin
mkdir -p $TMPPATH
cd $TMPPATH
set -e
if [ -f /var/lib/dpkg/status ]; then
   echo "# Your image is OE2.5/2.6 #"
   echo ""
   echo ""
else
   echo "# Your image is OE2.0 #"
   echo ""
   echo ""
fi
   wget https://github.com/ciefp/CiefpRottenTomatoes/archive/refs/heads/main.tar.gz
   tar -xzf main.tar.gz
   cp -r 'CiefpRottenTomatoes-main/usr' '/'
set +e
cd
sleep 2

### Check if plugin installed correctly
if [ ! -d $PLUGINPATH ]; then
	echo "Some thing wrong .. Plugin not installed"
	exit 1
fi

rm -rf $TMPPATH > /dev/null 2>&1
sync
echo ""
echo ""
echo "#########################################################"
echo "#        CiefpRottenTomatoes INSTALLED SUCCESSFULLY     #"
echo "#                  Version: $version                     #"
echo "#                  Changelog: $changelog                 #"
echo "#                  developed by ciefp                   #"
echo "#                  .::CiefpSettings::.                  #"
echo "#               https://github.com/ciefp                #"
echo "#########################################################"

# Only restart if SKIP_REBOOT is not set to 1
if [ "$SKIP_REBOOT" = "0" ]; then
    echo "#           your Device will RESTART Now                #"
    echo "#########################################################"
    sleep 5
    killall -9 enigma2
else
    echo "#        Restart skipped (batch installation)           #"
    echo "#########################################################"
fi

exit 0