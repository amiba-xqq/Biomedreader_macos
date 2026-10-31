on framePart(valueText)
    return (length of valueText as text) & linefeed & valueText
end framePart

on bridgeCall(parameter)
    set stageName to "parse request"
    set requestPath to ""
    try
        set delimiterPosition to offset of linefeed in parameter
        if delimiterPosition < 2 then error "Missing Word action"
        set actionName to text 1 thru (delimiterPosition - 1) of parameter
        set payload to ""
        if delimiterPosition < (length of parameter) then set payload to text (delimiterPosition + 1) thru -1 of parameter
        set executablePath to __BIOMED_EXECUTABLE__
        set userHome to __BIOMED_HOME__
        if actionName is "ping" then return my framePart("OK") & my framePart("PONG")
        set stageName to "create request file"
        set requestPath to do shell script "/usr/bin/mktemp -t BiomedReaderWord"
        set fileHandle to open for access POSIX file requestPath with write permission
        set eof fileHandle to 0
        write payload to fileHandle as «class utf8»
        close access fileHandle
        set stageName to "run Biomed Reader"
        set commandText to "/usr/bin/env " & quoted form of ("HOME=" & userHome) & " " & quoted form of executablePath & " --word-mac-command " & quoted form of actionName & " " & quoted form of requestPath
        set answer to do shell script commandText without altering line endings
        do shell script "/bin/rm -f " & quoted form of requestPath
        return answer
    on error errorText number errorNumber
        if requestPath is not "" then
            try
                close access POSIX file requestPath
            end try
            try
                do shell script "/bin/rm -f " & quoted form of requestPath
            end try
        end if
        set detailText to "Word bridge [" & stageName & "] error " & errorNumber & ": " & errorText
        return my framePart("ERROR") & my framePart(detailText)
    end try
end bridgeCall
