import sys

for line in sys.stdin:
     mdfname = line.strip()
     tgtfname = mdfname.replace(".md", ".pot")
     tgtf = "i18n/gettext/" + tgtfname

     cmd = "md-gettext extract " + mdfname + " " + tgtf
     print(cmd)
#    print(line, end="") 