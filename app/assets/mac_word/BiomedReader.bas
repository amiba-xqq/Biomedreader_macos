Attribute VB_Name = "BiomedReader"
Option Explicit
#Const BR_TEST = False
Private ribbon As Object
Private journals As Collection
Private currentJournal As String
Public Function BRProtocolTest() As String
    Dim data As String
    data = Part("DNA") & Part("ATP")
    BRProtocolTest = Take(data) & ":" & Take(data)
End Function

Private Function Part(ByVal value As String) As String
    Part = CStr(Len(value)) & vbLf & value
End Function
Private Function Take(ByRef data As String) As String
    Dim p As Long, n As Long
    p = InStr(data, vbLf)
    If p = 0 Then Err.Raise vbObjectError + 201, , "Invalid Biomed Reader response"
    n = CLng(Left$(data, p - 1)): data = Mid$(data, p + 1)
    If n < 0 Or n > Len(data) Then Err.Raise vbObjectError + 202, , "Truncated response"
    Take = Left$(data, n): data = Mid$(data, n + 1)
End Function
Private Function Bridge(ByVal action As String, ByVal payload As String) As String
    Dim answer As String, state As String
#If BR_TEST Then
    answer = ActiveDocument.Variables("BRTestResponse_" & action).Value
#ElseIf Mac Then
    answer = AppleScriptTask("BiomedReader.applescript", "bridgeCall", action & vbLf & payload)
#Else
    Err.Raise vbObjectError + 203, , "This template is for Word on Mac."
#End If
    state = Take(answer)
    If state <> "OK" Then Err.Raise vbObjectError + 204, , Take(answer)
    Bridge = answer
End Function
Private Sub PutVar(ByVal doc As Document, ByVal key As String, ByVal value As String)
    On Error Resume Next
    doc.Variables(key).Value = value
    If Err.Number <> 0 Then
        Err.Clear: doc.Variables.Add key, value
    End If
    On Error GoTo 0
End Sub
Private Function GetVar(ByVal doc As Document, ByVal key As String, ByVal fallback As String) As String
    On Error GoTo missing
    GetVar = doc.Variables(key).Value: Exit Function
missing:
    GetVar = fallback
End Function
Private Function CitationTag(ByVal item As Field) As String
    Dim code As String
    code = Trim$(Replace(Replace(item.Code.Text, vbCr, " "), vbTab, " "))
    If Left$(code, 18) = "DOCVARIABLE BRM_BR" Then CitationTag = Split(code, " ")(1)
End Function
Public Sub RibbonLoaded(ByVal value As Object)
    Set ribbon = value
    currentJournal = "Cell"
End Sub
Public Sub OpenReader(Optional ByVal control As Object)
    On Error GoTo failed
    Dim answer As String: answer = Bridge("show", Part("")): Exit Sub
failed:
    MsgBox Err.Description, vbExclamation, "Biomed Reader"
End Sub
Private Sub LoadStyles()
    If Not journals Is Nothing Then Exit Sub
    Dim answer As String: answer = Bridge("styles", Part(""))
    Set journals = New Collection
    Do While Len(answer) > 0: journals.Add Take(answer): Loop
End Sub
Public Sub StyleCount(ByVal control As Object, ByRef returnedVal)
    On Error GoTo failed
    LoadStyles: returnedVal = journals.Count: Exit Sub
failed:
    returnedVal = 0
End Sub
Public Sub StyleLabel(ByVal control As Object, ByVal index As Integer, ByRef returnedVal)
    On Error GoTo failed
    LoadStyles: returnedVal = journals(index + 1): Exit Sub
failed:
    returnedVal = ""
End Sub
Public Sub StyleText(ByVal control As Object, ByRef returnedVal)
    If currentJournal = "" Then currentJournal = "Cell"
    returnedVal = currentJournal
End Sub
Public Sub ChangeStyle(ByVal control As Object, ByVal value As String)
    On Error GoTo failed
    Dim title As Variant
    LoadStyles
    For Each title In journals
        If StrComp(CStr(title), value, vbTextCompare) = 0 Then
            currentJournal = CStr(title): Exit Sub
        End If
    Next
    MsgBox "请选择下拉目录中的期刊，或点击选择期刊按关键词搜索。", vbInformation, "Biomed Reader": Exit Sub
failed:
    MsgBox Err.Description, vbExclamation, "Biomed Reader"
End Sub
Public Sub SelectJournal(Optional ByVal control As Object)
    On Error GoTo failed
    LoadStyles
    Dim query As String, title As Variant, choices As New Collection, listing As String, answer As String, number As Long
    query = InputBox("输入期刊名称或部分关键词：", "参考文献格式")
    If query = "" Then Exit Sub
    For Each title In journals
        If InStr(1, CStr(title), query, vbTextCompare) > 0 Then choices.Add title
    Next
    If choices.Count = 0 Then MsgBox "没有匹配的期刊。": Exit Sub
    If choices.Count > 20 Then MsgBox "匹配过多，请输入更具体的期刊关键词。": Exit Sub
    For number = 1 To choices.Count: listing = listing & CStr(number) & ". " & choices(number) & vbCr: Next
    answer = InputBox(listing & vbCr & "输入期刊编号：", "选择期刊")
    If answer = "" Then Exit Sub
    number = CLng(answer)
    If number < 1 Or number > choices.Count Then Exit Sub
    currentJournal = choices(number)
    If Not ribbon Is Nothing Then ribbon.Invalidate
    Exit Sub
failed:
    MsgBox Err.Description, vbExclamation, "Biomed Reader"
End Sub
Public Sub InsertReference(Optional ByVal control As Object)
    On Error GoTo failed
    If Documents.Count = 0 Then Documents.Add
    If Selection.StoryType <> wdMainTextStory Then Err.Raise vbObjectError + 205, , "请将光标放在正文中。"
    Dim doc As Document, answer As String, record As String, tag As String, mark As Field, location As Range, cc As ContentControl
    Set doc = ActiveDocument
    For Each cc In doc.ContentControls
        If cc.Tag = "BiomedReaderBibliography" And Selection.InRange(cc.Range) Then Err.Raise vbObjectError + 206, , "请在参考文献列表之外插入。"
    Next
    answer = Bridge("selected", Part("")): record = Take(answer)
    Randomize: tag = "BRM_BR" & Format$(Now, "yyyymmddhhnnss") & CStr(Int(Rnd() * 1000000))
    PutVar doc, Mid$(tag, 5), record: PutVar doc, tag, "1"
    Set location = Selection.Range.Duplicate: location.Collapse wdCollapseEnd
    Dim existing As Field
    For Each existing In doc.Fields
        If CitationTag(existing) <> "" Then
            If location.Start >= existing.Code.Start - 1 And location.Start < existing.Result.End + 1 Then
                location.SetRange existing.Result.End + 1, existing.Result.End + 1: Exit For
            End If
        End If
    Next
    Set mark = doc.Fields.Add(location, wdFieldEmpty, "DOCVARIABLE " & tag, False)
    mark.Update: mark.Locked = True
    On Error GoTo rollback
    UpdateDocument doc
    doc.Range(mark.Result.End + 1, mark.Result.End + 1).Select
    Exit Sub
rollback:
    Dim message As String: message = Err.Description
    On Error Resume Next
    mark.Locked = False: doc.Range(mark.Code.Start - 1, mark.Result.End + 1).Delete
    doc.Variables(tag).Delete: doc.Variables(Mid$(tag, 5)).Delete
    On Error GoTo 0
    MsgBox message, vbExclamation, "Biomed Reader": Exit Sub
failed:
    MsgBox Err.Description, vbExclamation, "Biomed Reader"
End Sub
Public Sub UpdateReferences(Optional ByVal control As Object)
    On Error GoTo failed
    UpdateDocument ActiveDocument: Exit Sub
failed:
    MsgBox Err.Description, vbExclamation, "Biomed Reader"
End Sub
Private Sub ApplyFont(ByVal doc As Document)
    Dim item As Field, cc As ContentControl, name As String, size As Single, bold As Long, italic As Long
    name = GetVar(doc, "BRFontName", "Arial"): size = Val(Replace(GetVar(doc, "BRFontSize", "10.5"), ",", "."))
    bold = CLng(GetVar(doc, "BRFontBold", "0")): italic = CLng(GetVar(doc, "BRFontItalic", "0"))
    For Each item In doc.Fields
        If CitationTag(item) <> "" Or InStr(item.Code.Text, "ADDIN EN.") > 0 Then
            item.Result.Font.Name = name: item.Result.Font.Size = size: item.Result.Font.Bold = bold: item.Result.Font.Italic = italic
            If InStr(item.Code.Text, "EN.REFLIST") > 0 Then item.Result.Font.Superscript = False
        End If
    Next
    For Each cc In doc.ContentControls
        If cc.Tag = "BiomedReaderBibliography" Then
            cc.Range.Font.Name = name: cc.Range.Font.Size = size: cc.Range.Font.Bold = bold: cc.Range.Font.Italic = italic: cc.Range.Font.Superscript = False
        End If
    Next
End Sub
Private Sub UpdateDocument(ByVal doc As Document)
    Dim items As New Collection, item As Field, tag As String, payload As String, answer As String, count As Long, i As Long
    If currentJournal = "" Then currentJournal = "Cell"
    payload = Part(currentJournal)
    For Each item In doc.Fields
        tag = CitationTag(item)
        If tag <> "" Then
            items.Add item: payload = payload & Part(doc.Variables(Mid$(tag, 5)).Value)
        End If
    Next
    answer = Bridge("render", payload): count = CLng(Take(answer))
    If count <> items.Count Then Err.Raise vbObjectError + 207, , "引文响应数量不一致，未修改文档。"
    Dim valid() As String, markers() As String, supers() As String, records() As String, xml() As String, bibliography As String
    ReDim valid(1 To count + 1): ReDim markers(1 To count + 1): ReDim supers(1 To count + 1): ReDim records(1 To count + 1): ReDim xml(1 To count + 1)
    For i = 1 To count
        valid(i) = Take(answer): markers(i) = Take(answer): supers(i) = Take(answer): records(i) = Take(answer): xml(i) = Take(answer)
    Next
    bibliography = Replace(Take(answer), vbLf, vbCr)
    For i = count To 1 Step -1
        Set item = items(i): tag = CitationTag(item)
        If valid(i) = "0" Then
            doc.Range(item.Code.Start - 1, item.Result.End + 1).Delete
            doc.Variables(tag).Delete: doc.Variables(Mid$(tag, 5)).Delete
        Else
            PutVar doc, Mid$(tag, 5), records(i): PutVar doc, tag, markers(i): PutVar doc, "BRE_" & tag, xml(i)
            item.Locked = False: item.Update: item.Result.Font.Superscript = (supers(i) = "1"): item.Locked = True
        End If
    Next
    Dim cc As ContentControl, list As ContentControl, endRange As Range
    For Each cc In doc.ContentControls
        If cc.Tag = "BiomedReaderBibliography" Then Set list = cc
    Next
    If Not list Is Nothing Then
        If Len(Trim$(doc.Range(list.Range.End, doc.Content.End - 1).Text)) > 0 Then list.Delete True: Set list = Nothing
    End If
    If list Is Nothing Then
        Set endRange = doc.Content: endRange.Collapse wdCollapseEnd: endRange.InsertBefore vbCr
        Set endRange = doc.Range(doc.Content.End - 1, doc.Content.End - 1)
        Set list = doc.ContentControls.Add(wdContentControlRichText, endRange): list.Tag = "BiomedReaderBibliography": list.Title = "Biomed Reader references"
    End If
    list.Range.Text = bibliography
    ApplyFont doc
End Sub
Public Sub ChooseFont(Optional ByVal control As Object)
    On Error GoTo failed
    Dim selected As Range, name As String, size As Single, bold As Long, italic As Long
    Set selected = Selection.Range.Duplicate
    With Dialogs(wdDialogFormatFont)
        .Font = GetVar(ActiveDocument, "BRFontName", "Arial"): .Points = Val(Replace(GetVar(ActiveDocument, "BRFontSize", "10.5"), ",", "."))
        .Bold = CLng(GetVar(ActiveDocument, "BRFontBold", "0")): .Italic = CLng(GetVar(ActiveDocument, "BRFontItalic", "0"))
        If .Display <> -1 Then Exit Sub
        name = .Font: size = .Points: bold = .Bold: italic = .Italic
    End With
    PutVar ActiveDocument, "BRFontName", name: PutVar ActiveDocument, "BRFontSize", Replace(CStr(size), ",", ".")
    PutVar ActiveDocument, "BRFontBold", CStr(bold): PutVar ActiveDocument, "BRFontItalic", CStr(italic)
    ApplyFont ActiveDocument: selected.Select: Exit Sub
failed:
    MsgBox Err.Description, vbExclamation, "Biomed Reader"
End Sub
Public Sub ExportEndNote(Optional ByVal control As Object)
    On Error GoTo failed
    Dim source As Document, copy As Document, value As Variable, item As Field, tag As String, code As String, cc As ContentControl, cached As String, r As Range
    Set source = ActiveDocument: UpdateDocument source
    Set copy = Documents.Add: copy.Content.FormattedText = source.Content.FormattedText
    For Each value In source.Variables
        If Left$(value.Name, 2) = "BR" Then PutVar copy, value.Name, value.Value
    Next
    Dim i As Long, total As Long
    For i = copy.Fields.Count To 1 Step -1
        Set item = copy.Fields(i): tag = CitationTag(item)
        If tag <> "" Then
            code = copy.Variables("BRE_" & tag).Value
            item.Locked = False: item.Code.Text = " ADDIN EN.CITE " & code & " ": item.Locked = False: total = total + 1
        End If
    Next
    If total = 0 Then copy.Close wdDoNotSaveChanges: Err.Raise vbObjectError + 208, , "正文中没有Biomed Reader引文。"
    For i = copy.ContentControls.Count To 1 Step -1
        Set cc = copy.ContentControls(i)
        If cc.Tag = "BiomedReaderBibliography" Then
            cached = cc.Range.Text: Set r = cc.Range.Duplicate: cc.Delete False
            PutVar copy, "BRExportReferences", cached
            Set item = copy.Fields.Add(r, wdFieldEmpty, "DOCVARIABLE BRExportReferences", False)
            item.Update: item.Code.Text = " ADDIN EN.REFLIST ": item.Locked = False
        End If
    Next
#If Not BR_TEST Then
    On Error Resume Next
    Application.Run "ENFormatBibliography"
    Err.Clear
    On Error GoTo failed
#End If
    ApplyFont copy
#If BR_TEST Then
    copy.SaveAs2 source.Variables("BRTestOutput").Value, wdFormatXMLDocument
#Else
    If Dialogs(wdDialogFileSaveAs).Show = -1 Then MsgBox "已导出含嵌入文献记录与文末参考文献的Word文档。请在EndNote选项卡核对并更新引文。原文档未转换为EndNote域。", vbInformation, "Biomed Reader"
#End If
    Exit Sub
failed:
    MsgBox Err.Description, vbExclamation, "Biomed Reader"
End Sub
