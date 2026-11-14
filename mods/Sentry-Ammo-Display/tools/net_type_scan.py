"""诊断补丁：把场上每个网络对象的真实类型扫出来，供离线定位未绑定模型。

类型 id 是 32 位（Network.IDSTRING32），名字只要 murmur64 的高 32 位对上就能解析，
下面的名字由 build/_scan 的搜索器为全部 468 条 network_config 记录各算出一个。

表头 23 个是模组原本就在用的真名，既当对照组（验证自造名与真名是否等效），
也让炮塔对象在头几次调用就命中、不必扫完整张表。
只有该类型确实声明了位置字段（`c{i(uTh>`）时才去读坐标，否则只报类型。
扫出结果后按 goid 记住，后续轮次只补测新出现的对象。验证完即删。
"""

SCAN = r"""
-- 诊断用：全类型扫描。前 SCAN_REAL 个是模组自带真名，其余由 32 位 hash 碰撞生成。
local SCAN_NAMES={
    'zdZ~yhg!','Es}/~kND','i1j-1MHz','T[tD_-{~','N+d%Kq$1','4%EXhX(8',
    'mZ(LD/!&','941ND^v~','):e&;~2_','oYVe)p+i','JMR4x`Ao','G`Y1$voS',
    'Og,VB9b3','}>|mEsa.','$Leg(yqP','ok$F9qrS','9I~t$THc','Mm{:#c*%',
    '<j9*h3.S','<3hkntZ?','tXoU]Yb{','h:(x&X8#','f$XD&/}6','!!!*Xbo`',
    '!!#%|?i%','!!!}eItu','!!!&oQdd','!!!Zr4)C','!!#+pj~y','!!$?+htx',
    '!!!SNq5/','!!!&TFBn','!!!O_M+e','!!!}CJtT','!!#l?inc','!!!(0?og',
    '!!!%TsR$','!!!.ri5R','!!#N<kvX','!!!I>J%t','!!#F:(G;','!!!2Qp5a',
    '!!!(f^q0','!!#tVor|','!!%S.l.n','!!!076KW','!!!CQ|)n','!!!nS,VR',
    '!!#P2b!A','!!!(SG+;','!!!FTj2t','!!!hLEIq','!!!,DRPw','!!!S!jJ(',
    '!!#/s.9K','!!!CgjuO','!!!m>#<4','!!!?T:1R','!!!/Nycs','!!!nmKQS',
    '!!#0[6Ki','!!!D/]Y0','!!%1[c{I','!!$1rGUk','!!!i8;Ax','!!!}mBdQ',
    '!!!:^yT5','!!!UAg&H','!!!*+Lmx','!!!*iQO-','!!!3MS~~','!!!yv1N!',
    '!!!:Z[s;','!!$2qdS6','!!!Jd7QF','!!!yz6.F','!!!6nV+I','!!!a}!;t',
    '!!!%OOWO','!!$Iu^tl','!!#R2gPt','!!#F?E|o','!!#(w9PI','!!!C@;8J',
    '!!#gM.XI','!!!HspVN','!!#zL,WR','!!!>j;{;','!!!(a)l_','!!!&JV5K',
    '!!!pJh]b','!!!5mMqb','!!!sS/,I','!!#H:oyg','!!$32@jL','!!!`,gdg',
    '!!#!J#R7','!!!00~E/','!!!50KFr','!!!|cBa#','!!!-et-=','!!)I]Oub',
    '!!!,2(~+','!!!F-a|v','!!!qBUtp','!!!X;jTK','!!!@[,A,','!!!*~Qur',
    '!!#y7#4N','!!#,roD{','!!!ACIn$','!!!lJNNB','!!#JyED)','!!!#L+S0',
    '!!!-d{/C','!!!#gBZZ','!!!2)y4?','!!!Z,oja','!!#!OYRd','!!!;y0#%',
    '!!!&z/;J','!!!)#?MM','!!!F)e?%','!!!Q1#6}','!!!>$X5b','!!!0$6%w',
    '!!!-k[+U','!!!mCOQH','!!!PrBGW','!!!ulJM`','!!#+e.d}','!!#!LNYX',
    '!!!CqDXv','!!!g`vBJ','!!#RzoEp','!!!Cq:>d','!!!$XArw','!!!-^QO&',
    '!!!Km{g!','!!!&y$m/','!!#*X[vV','!!!+m2A=','!!#*FOO`','!!#W!)HA',
    '!!!/sqVv','!!!ud:H[','!!!YUV2W','!!!$g7m$','!!!0**![','!!#iDSnv',
    '!!!B%vlJ','!!!HW]v3','!!!#H&3~','!!$iVY.*','!!!t,C<<','!!!-=h5J',
    '!!#-V!sT','!!!@E%*L','!!!$-M*2','!!#*Js`)','!!!`rOCu','!!!$~u{i',
    '!!#Lc=OS','!!!!:ZFK','!!!zs#6Z','!!#v<(r$','!!!H[UA2','!!!vAK4T',
    '!!#wMqg/','!!!Ov=6!','!!!)g|p`','!!!zl[nD','!!!m9<Ni','!!!4NMZd',
    '!!!8/@YE','!!#0G9u%','!!!%Uozg','!!#>f/aA','!!!RD,rj','!!#Hz]fZ',
    '!!!igVl:','!!!m%xJB','!!!b8#QZ','!!!@[]xn','!!!g<yhR','!!!UFE$)',
    '!!!Yr=6A','!!!*4cp*','!!!Wy60W','!!#5s/cR','!!!|#959','!!!$C%kw',
    '!!#I@QWu','!!!)J_RX','!!!b|]8d','!!#R[hk(','!!#^lE]X','!!#8HI#-',
    '!!#%jL)Q','!!!m=W`G','!!$-s/Hs','!!!`_?]u','!!!R11W7','!!!PB0sJ',
    '!!!N^u.}','!!!kl5yB','!!!~TQr(','!!!&P_GX','!!!TS}&>','!!!DK[Gz',
    '!!!quMaL','!!!zFmf9','!!!bq,aL','!!!0`dOs','!!!6~jvk','!!!(,%<u',
    '!!#q:<_d','!!!?BTXY','!!$<73!c','!!#a,OyW','!!#5kBMA','!!!yE?Hd',
    '!!!:-IlX','!!$.>=zp','!!!VWg:]','!!$$9aU^','!!!~Wx~J','!!!m@a+A',
    '!!!&FWme','!!!!3qh3','!!!@xU>/','!!!,[zyI','!!!rdt:h','!!!O(+ja',
    '!!!SUh(]','!!!#n}Bv','!!#>E_hD','!!!lsg/6','!!!6jU1$','!!!.]PJ[',
    '!!!IIIU?','!!#[I)/e','!!!`>mVX','!!!5>B0^','!!!.!!b7','!!%)%uGz',
    '!!$C}huC','!!#f+F$y','!!!1:(*;','!!$I.*}U','!!!6&Ukk','!!!;m~xq',
    '!!!#GIo>','!!!)fZiG','!!#GoQKb','!!!;piVn','!!!?RLS;','!!#=rDY,',
    '!!$z]?fE','!!!4/;Xp','!!!a(Dbf','!!!e$DnB','!!!J4+au','!!!/YHj3',
    '!!!f!<kn','!!!4K#gI','!!!$Qh2;','!!!,s/Hc','!!!XwZo+','!!#:wkts',
    '!!!4M2.S','!!!3cE1_','!!!)#+!?','!!!#ye.u','!!!g5pM2','!!!LDp:Y',
    '!!$QLMf[','!!!.1g-2','!!!9;^Tv','!!!(JMRA','!!!)pVKZ','!!!sS}{(',
    '!!!_VHo,','!!!6<ZjW','!!!j/$Sl','!!!etlK+','!!!!/H_~','!!!TX{:r',
    '!!!R<sut','!!!94pr#','!!!`9MUA','!!!=1%*1','!!!+^;G6','!!$RPjPz',
    '!!!I01=Y','!!!c$;Fk','!!!Lq>?Q','!!!8jlFS','!!!J!V)m','!!!!(/-b',
    '!!!0Y]AH','!!!$MbJa','!!!7~hu=','!!!t{!/G','!!!L{biO','!!!=ey%?',
    '!!#D>fk}','!!!&+![%','!!#8Y[Ou','!!#IQ|+^','!!!aRh8E','!!#.F;)z',
    '!!!>zS+X','!!#eX)$f','!!#DH2ql','!!$qtg2`','!!!3l/j=','!!!Rtl|+',
    '!!!P+#qh','!!!bkY3]','!!!ahP^k','!!#K^%Nc','!!#E#$LZ','!!!S1FpR',
    '!!!{CXF]','!!!@?7V;','!!!B2_#?','!!!^^dC*','!!!MDI/2','!!!@_FRm',
    '!!!3?^A;','!!#*Dt`#','!!#t[wN`','!!#3I49i','!!!1qx;H','!!#2@)7y',
    '!!!_)18k','!!!.wuoL','!!!nV9;u','!!!)GUN1','!!!4V*D3','!!!s;-bQ',
    '!!#?i-v;','!!!1EOI}','!!!DKCs3','!!!&avfz','!!!4@Yy$','!!!%W/y.',
    '!!!-71Kb','!!!}ah{n','!!$^Hh({','!!!PQ&c!','!!!q*t4k','!!!Bz&!~',
    '!!!<VM|T','!!$#)BK=','!!!%(O&J','!!!#K4#F','!!!<bD9E','!!!5Hbq-',
    '!!!6oKJ4','!!!/r{Z|','!!!VFBg_','!!#eqtl,','!!!G({4+','!!!;nrlI',
    '!!!D|{5)','!!!NZtx9','!!!2wYlM','!!$96%9:','!!!X:}`R','!!!a5f/r',
    '!!!d--IW','!!!Sp0C{','!!!#ZaOD','!!!9-!Sd','!!!l,z#_','!!!8Ca&K',
    '!!#0HR&Q','!!!qw&lD','!!!8P!i@','!!!I/QO7','!!!kgB`S','!!!lOOlQ',
    '!!![x[M0','!!!!?9j9','!!!Q]4=A','!!!h<A[C','!!!gkUSC','!!!eT;W2',
    '!!##d!l[','!!!<xGzR','!!!<*9Nf','!!!v@|br','!!!6[XmX','!!!(5WLJ',
    '!!!q-A*w','!!!Qn+uR','!!#,43if','!!!$Q>{z','!!!&v45.','!!!.Y7%)',
    '!!#m|@Wj','!!!!Ve:5','!!$=/OP5','!!!1-.P@','!!!Q^nbk','!!!3-|Ru',
    '!!#Ye!qR','!!!m|CD?','!!#ySL9^','!!#8R`:7','!!!H<Ov1','!!!BYsB:',
    '!!!=$!/a','!!!nM7(}','!!!(,Y&J','!!!R9Hpv','!!!]i-US','!!%&mG)O',
    '!!!>%=dy','!!!+5Wu9','!!!P7c,K','!!!9dfFX','!!!)R2xv','!!!&hUSA',
    '!!!H3=kE','!!!ADj?`','!!!)-4&P','!!!3eSNz','!!!e]Wb5','!!!+Xyvt',
    '!!!H.pxd','!!!*{n*M','!!!;SeKR','!!!J.?JX','!!#/CA;q','!!#T$iD)',
    '!!!#d9yj','!!!:S1iU','!!#7}yo:','!!!Sf|fl','!!#Tt`iT','!!!Ik?l)',
    '!!!^,F8_','!!#A_,G#','!!!2suD-','!!!nLa;p','!!!i(}~-','!!!*0%gm',
    '!!!5__eE','!!!b8b%h','!!!HUSw8','!!!>3I{r','!!!6/bj4','!!!&J3c@',
    '!!!^wDih','!!!Cu_:0','!!!:+RFs','!!!48cK^','!!!0S%hc','!!!Z-)/H',
    '!!!$$KYw','!!##)]T@','!!!L*u{h','!!!e.m)9','!!!{$#SU','!!!:jBQb',
    '!!!)Mw>:','!!#lq<.R','!!!3{`;=','!!!6Sk,|','!!!+!|e&','!!!D{TPL',
    '!!!2*QE*','!!#qGc@K','!!!|32y[','!!!FjJ{:','!!#,|{7M','!!!%cz9.',
    '!!!?bkn-','!!!I1|MN','!!!N~wu)','!!#Royae','!!!cylcX','!!#1[9j9',
    '!!#%CY<K','!!#.?{hr','!!!~Lbbp','!!!Qx%w)','!!#iigOq',
}
local SCAN_HASH={
    '0162dd9e','13a71430','14c9c273','2363e364','2596fa0a','3416fff4',
    '3ae02f80','3bd04c18','3c709c50','6d3f8345','947ed108','992ff297',
    '9d474323','9fdd94c0','a37f0894','b551b138','be95e875','bf35608c',
    'ec516bbd','ee956607','ef108792','f1dbc135','f53d4725','0162dd9e',
    '01ab3e6d','048ce2de','07911f97','07b51587','0df416d3','10662676',
    '118f3c7c','13a71430','14c9c273','165fc36e','18a01c11','1958acb5',
    '1c7ca26c','1f37dab0','2199ab14','21cc854c','2299bf2f','2363e364',
    '251a4704','2596fa0a','28e9e58f','2c1744f8','2d3e1d28','3030a4d2',
    '316595a3','31e8e62d','3416fff4','358ef508','36853a8e','3926f217',
    '3ae02f80','3bd04c18','3c709c50','3ce90d0e','3d65d411','3f3d1d22',
    '401c4ef1','40b7da1a','42f7db08','46d7c60e','49d7859c','4aa644a9',
    '4e64c4dc','4ea4ba77','52e0b352','53ae74ee','5b56efd2','5db58a35',
    '5db9387c','65e4986b','6d3f8345','6d745627','6ddbf2ae','6f4709b5',
    '71480bc3','74c60ac0','7a66ef6c','7aa2db54','7d736a8b','7e40750e',
    '88496cde','8e23220e','9075faf0','947ed108','95ce31b6','992ff297',
    '9a28415a','9d474323','9fdd94c0','a37f0894','a8282593','a98a506a',
    'ab1f1577','abbd222f','ae41dcfe','b551b138','b9acb226','be95e875',
    'bf35608c','c208c35d','c5321867','c704e0f9','c7970a97','c9099597',
    'ca34a097','cab3a3cf','ce4427b2','cfaa6860','d0b36b3b','d55e91c6',
    'd677753d','dc715109','de88a043','dfa631b9','ec516bbd','ee956607',
    'ef108792','f0d154d1','f1dbc135','f32cebf7','f37e3ebf','f53d4725',
    'f5915517','f7bfb8db','fb8c4465','0113bc49','02444e51','02e7c3f2',
    '0303c9a0','03547194','038a00cc','0422a6f2','04645757','04f392ce',
    '05256e47','055bb169','057f233e','06887ef8','06a1047a','06fee72c',
    '07bd233c','086d544c','0a482e89','0a84375b','0be23e31','0bee9119',
    '0c10c622','0c3c0eb3','0cb747b7','0cf47b26','0dff968f','0ea37e5d',
    '0f1c7231','107fa9fc','11d8c98b','12584790','1310d067','14c75f20',
    '15ae0889','17783758','17b02218','17d1f072','17db8107','18ba422a',
    '193ffddf','194bdfa6','1962a5a9','1ab72be4','1ab955e9','1bc830af',
    '1bec6c1a','1cb22371','1d301481','1f3765c1','20c29e45','20ec5b6f',
    '228e0659','22929ccb','2432c30a','2444bbce','26acc614','26eccc06',
    '2ac9b588','2c0c01f7','2c341346','2c4d7b3c','2cef5e3b','2d552a28',
    '2ddd091d','2df95dfe','2e4749c7','2ef8c28a','2f027b59','2fad831f',
    '3000b371','30294946','303d2220','30b55bbc','312febbf','314a1bf5',
    '3244dc49','32fba243','336e46ba','3478da46','34e1bc3a','350dc8fa',
    '3622f8dc','36433ca8','366d3622','37193958','371c20a3','388b859b',
    '396381a9','39a71a1d','3b2e3645','3b718900','3bf357e5','3c16b6ef',
    '3c189aa5','3c3cec57','3cecce6c','3d191f7a','3d538455','3d7ed060',
    '3df247f4','3f79f6e2','3f87a7e8','3fb302d8','41386847','41d645a7',
    '41de83ec','422c03cb','425bb7e3','4321fb69','4796fd39','47c89128',
    '48d9a540','499fd7a8','4a62a3a9','4cf7fdda','4daea8e1','4ee55ac0',
    '520b5165','523bde80','532cf6bd','53dc48fe','54fb3142','5590232e',
    '5652ed49','57f34b28','58245c0c','58594a4a','58feebf5','59044eaa',
    '5aa3bfa1','5b1b61bf','5b5264ec','5bdc7b24','5c661180','5c813e43',
    '5c957a4f','5d52c1d0','5d9752f9','5dfc55bb','5e0cd6f6','5e95439c',
    '5e9637d0','5f3517bf','605a5bb9','60cac78a','611b6bf9','64f91671',
    '656d507b','66611cce','6876b30a','6947919e','69971ef9','6a073cc6',
    '6b2c3bed','6c26a65d','6cc38f79','6e2ed40c','6ec2a850','6f0a0db2',
    '6f5ed5c0','716fc7fe','7405f976','74a2e57a','7559f271','767d2004',
    '76b90dcd','77dc330e','79bc04a2','7a694dd3','7b729b3a','7bfb2e06',
    '7c2f5100','7d16f96c','7d9a46dc','7ea9ed9a','7f4b0695','803159b3',
    '80b2dcfd','80d3298e','814c5e3d','81583e65','82a8d3c2','83d1036e',
    '8583e435','866289c6','870a7011','8859444f','89a4486a','8a3c6fee',
    '8a71b839','8b2ee1ef','8b40a204','8b50028e','8c0aee6e','8c299f75',
    '8c51d16e','8c57c12f','8c6aebd1','8faefd04','8fcdd35c','919e45b3',
    '91dd0f48','91f10108','942a0601','94dd9957','95c5dc52','960653cb',
    '9652785b','967a3fed','96c78961','96c8cffd','96cb35d7','96e240f2',
    '97425204','986600d1','9a2b9962','9c06f074','9c2e7f43','9c6bc008',
    '9ca7fac8','9ce82ac5','9d76c858','9dbc9204','9e122e44','9e18f0cf',
    '9f16a5a2','a09f5a5c','a261d7f0','a327969b','a41ddfcd','a4e5b53d',
    'a4eff4fa','a523ef8a','a5fcd71d','a6445802','a7d06d7b','a821c752',
    'a8dda382','aa907e6c','aac34d6b','ab04027e','ab38cade','aca565f4',
    'ad0dc3c8','ae35e7d7','ae9503a3','aeaf939f','aef68099','b060f4a4',
    'b13a8345','b2bfd847','b30f446f','b3246bb7','b5badd9f','b66d1624',
    'b697955d','b7574b90','b936384d','ba161726','bae3f0a6','bb2729e9',
    'bd5f17dd','bd69189c','be010cc7','be9b793e','beb09041','bf749fb2',
    'c05dbc1b','c0693f8c','c0e5aa7f','c0ff5282','c200d4f5','c204018b',
    'c2556456','c3457794','c3fd169b','c4235b66','c573e903','c5a989c9',
    'c61aae4c','c656df34','c7dfd9eb','c8102937','c828dca5','c920c2d3',
    'c9680f06','c9cc2fac','ca367ec7','ca6aa021','cd0e4b4a','cdb1e951',
    'cdccbaa6','cee3775a','cfaa9ddc','d020931a','d0454672','d100e0c8',
    'd126f103','d1a2adab','d1bf765a','d372022f','d3c9a08c','d4398a6d',
    'd496b132','d5e0448b','d675eb10','d6f22bc8','d724a2f9','d7dde809',
    'db0fe7b4','db56604e','dd163572','dd9f1091','ddb625c5','de0cb0ab',
    'decce39b','deeeea0b','def25ee4','e18d64e9','e1f10066','e2ab08d5',
    'e32ff89c','e3d29b1f','e42d5902','e645b6ee','e86fb7d6','ea3cabd2',
    'eaa3a6da','eb38d34e','eb621141','eb784ac7','ebc18699','ec698151',
    'ef8fb68e','ef90a60b','efa02c2f','f179599a','f259751e','f36a1cd6',
    'f3d3ab0e','f439bdce','f45000d7','f45f531b','f4e6c4a4','f5553215',
    'f7bd2602','f96b8f11','f9bb2f35','fbbfc6a3','fbc7f2e1','fbd58864',
    'fc0812f8','fc1ece86','fd115815','fd585726','fde8ab3d','fe07a595',
    'fe16d7e6','fe760204','ff14374b','ff3cf8d8','ffc5251f',
}
local SCAN_POS='11111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111100000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000'
local SCAN_REAL=23             -- 表头几个是真名，命中的第一个用来校验自造名

local SCAN_BUDGET=120          -- 每帧最多几次 is_type
local SCAN_FIRST=15           -- 进图后多少秒开始第一轮
local SCAN_REFRESH=10         -- 每轮结束后隔多久重新取对象池
local SCAN_MIN_POOL=8         -- 对象太少时先等，避免在空图里白扫

local scan_known={}           -- goid -> 类型 hash（'?' 表示扫过但没有匹配）
local scan_call={}            -- goid -> 命中时用的 call 名（读字段要用它）
local scan_session,scan_ids=nil,nil
local scan_i,scan_j=1,1
local scan_passes,scan_new,scan_objs=0,0,0
local scan_started,scan_next_pool=nil,nil
local scan_last_new,scan_logged_objs=0,-1
local scan_checked=false
local scan_xcheck=nil            -- nil 未验证 / true 自造名与真名等效 / false 不等效

local function scan_log(fmt,...)
    if type(ot.log)=='function' then pcall(ot.log,fmt,...) end
end

local function scan_pool()
    local S=type(ot.engine)=='table' and ot.engine or {}
    local Net,GS=S.Network or {},S.GameSession or {}
    if type(Net.game_session)~='function' or type(GS.peers)~='function'
        or type(GS.objects_owned_by)~='function' then return nil end
    local ok,session=pcall(Net.game_session)
    if not ok or session==nil then return nil end
    local good,peers=pcall(GS.peers,session)
    if not good or type(peers)~='table' then return nil end
    local ids,n={},0
    for _,peer in pairs(peers) do
        local got,list=pcall(GS.objects_owned_by,session,peer)
        if got and type(list)=='table' then
            for k=1,#list do
                n=n+1
                if n>512 then break end
                ids[n]=list[k]
            end
        end
    end
    return session,ids
end

local function scan_pos(session,id)
    local V3=ot.engine.Vector3
    if type(V3)~='table' or type(V3.to_elements)~='function' then return nil end
    local ok,value=pcall(ot.engine.GameSession.game_object_field,session,id,'c{i(uTh>')
    if not ok or value==nil then return nil end
    local good,x,y,z=pcall(V3.to_elements,value)
    if not good or type(x)~='number' or type(y)~='number' or type(z)~='number' then return nil end
    return string.format('(%.2f,%.2f,%.2f)',x,y,z)
end

local function scan_tick(now)
    if scan_started==nil then scan_started=now end
    if not scan_checked then
        scan_checked=true
        local Net=ot.engine.Network
        local function fields_of(name)
            if type(Net)~='table' or type(Net.object_info)~='function' then return -1 end
            local ok,info=pcall(Net.object_info,name)
            if not ok or type(info)~='table' or type(info.fields)~='table' then return -1 end
            local n=0
            for slot in pairs(info.fields) do
                if type(slot)=='number' then n=n+1 end
            end
            return n
        end
        scan_log('SENTRY-SCAN selfcheck real=%d made=%d',
            fields_of(SCAN_NAMES[1]),fields_of(SCAN_NAMES[SCAN_REAL+1]))
    end
    if now-scan_started<SCAN_FIRST then return end
    if scan_ids==nil then
        if scan_next_pool~=nil and now<scan_next_pool then return end
        local session,ids=scan_pool()
        scan_next_pool=now+SCAN_REFRESH
        local n=ids and #ids or -1
        if session==nil or ids==nil or n<SCAN_MIN_POOL then
            if n~=scan_logged_objs then
                scan_logged_objs=n
                scan_log('SENTRY-SCAN pool waiting objects=%d',n)
            end
            return
        end
        scan_session,scan_ids=session,ids
        scan_i,scan_j=1,1
        scan_objs=n
        if n~=scan_logged_objs or scan_passes==0 then
            scan_logged_objs=n
            scan_log('SENTRY-SCAN pool objects=%d types=%d pass=%d',n,#SCAN_NAMES,scan_passes+1)
        end
        return
    end
    local GS=ot.engine.GameSession
    local budget=SCAN_BUDGET
    while budget>0 and scan_i<=#scan_ids do
        local id=scan_ids[scan_i]
        local known=scan_known[id]
        if known~=nil then
            scan_i,scan_j=scan_i+1,1
        else
            local hit=nil
            while scan_j<=#SCAN_NAMES and budget>0 do
                local ok,match=pcall(GS.game_object_is_type,scan_session,id,SCAN_NAMES[scan_j])
                budget=budget-1
                if ok and match then hit=scan_j;break end
                scan_j=scan_j+1
            end
            if hit then
                local hash=SCAN_HASH[hit]
                if scan_xcheck==nil and hit<=SCAN_REAL then
                    for k=SCAN_REAL+1,#SCAN_NAMES do
                        if SCAN_HASH[k]==hash then
                            local mok,mmatch=pcall(GS.game_object_is_type,scan_session,id,SCAN_NAMES[k])
                            scan_xcheck=(mok and mmatch==true)
                            scan_log('SENTRY-SCAN xcheck type=%s real=ok made=%s',hash,tostring(mok and mmatch))
                            break
                        end
                    end
                end
                local where=' none'
                if SCAN_POS:sub(hit,hit)=='1' then where=scan_pos(scan_session,id) or ' unreadable' end
                scan_known[id]=hash
                scan_call[id]=SCAN_NAMES[hit]
                scan_new=scan_new+1
                scan_log('SENTRY-TYPE goid=%s type=%s pos=%s',tostring(id),hash,where)
                scan_i,scan_j=scan_i+1,1
            elseif scan_j>#SCAN_NAMES then
                scan_known[id]='?'
                scan_i,scan_j=scan_i+1,1
            end
        end
    end
    if scan_i>#scan_ids then
        scan_ids=nil
        scan_passes=scan_passes+1
        scan_next_pool=now+SCAN_REFRESH
        if scan_new>scan_last_new then
            scan_log('SENTRY-SCAN pass done objects=%d new=%d passes=%d',
                scan_objs,scan_new-scan_last_new,scan_passes)
        end
        scan_last_new=scan_new
    end
end
"""
