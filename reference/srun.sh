#!/bin/bash

sql="$(cygpath -m -a $1)" ; shift

if [ "$1" == "--verbose" ] || [ "$1" == "-v" ] ; then
    shift
    echo "+ sqlplus -S '${DB_USERNAME}/--password--@${DB_NAME}' @'${sql}'  $@"
fi

echo quit | sqlplus -S "${DB_USERNAME}/${DB_PASSWORD}@${DB_NAME}" @"${sql}"  "$@"
