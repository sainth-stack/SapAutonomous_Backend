from pydantic import BaseModel
from datetime import date

class SlaTicketCreate(BaseModel):
    ReqCreationDate : date
    CreationTime : str
    ReqCreationDateYearWeekISO : str
    RequestID : str
    RequestPriorityDescription : str
    HistoricalStatusStatusFrom : str
    HistoricalStatusStatusTo : str
    HistoricalStatusChangeDate : date
    HistoricalStatusChangeTime : str
    MacroAreaName : str
    RequestResourceAssignedToGROUPSAPMD : str
    MacroArea : str
    RequestUserName : str
    RequestResourceAssignedToName : str
    ReqTypeDescription : str
    ReqStatusDescription : str
    ReqClosingDate : date
    RequestTextRequest : str
    RequestTextAnswer : str
    RequestCategory : str
    RequestSubjectdescription : str