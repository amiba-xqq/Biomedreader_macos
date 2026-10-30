on bridgeCall(parameter)
    set delimiterPosition to offset of linefeed in parameter
    if delimiterPosition is 0 then error "Missing Word action"
    set actionName to text 1 thru (delimiterPosition - 1) of parameter
    set payload to text (delimiterPosition + 1) thru -1 of parameter
    set configPath to (POSIX path of (path to home folder)) & "Library/Application Support/OrangeReader/WordBridge/app-path.txt"
    set executablePath to read POSIX file configPath as «class utf8»
    set requestPath to do shell script "/usr/bin/mktemp -t BiomedReaderWord"
    try
        set fileHandle to open for access POSIX file requestPath with write permission
        set eof fileHandle to 0
        write payload to fileHandle as «class utf8»
        close access fileHandle
        set answer to do shell script (quoted form of executablePath & " --word-mac-command " & quoted form of actionName & " " & quoted form of requestPath) without altering line endings
        do shell script "/bin/rm -f " & quoted form of requestPath
        return answer
    on error errorText number errorNumber
        try
            close access POSIX file requestPath
        end try
        do shell script "/bin/rm -f " & quoted form of requestPath
        error errorText number errorNumber
    end try
end bridgeCall
