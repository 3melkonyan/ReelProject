"""Metadata constraints independent of the local language model."""
import copy,json,math
from pathlib import Path
import pytest
from reel.stages import metadata

TOPICS=['this-ի վարքը','Scope-ի կանոնները','Arrow function-ի տարբերությունը','call և apply մեթոդները','bind-ի կապակցումը','Closure-ի տվյալները','Bytecode-ի կառուցվածքը','Interpreter-ի աշխատանքը','Hoisting-ի կանոնները','Prototype-ի շղթան','Object-ի վիճակը','Class-ի կառուցումը','Context-ի փոփոխումը','Lexical environment-ի պահպանումը','Memory-ի օգտագործումը','Arguments-ի փոխանցումը']
ENGLISH_TOPICS=['Function Scope and this Binding','Dynamic this Resolution','Function Declaration',
 'Arrow Function Performance','Compiler Optimization','Interpreter Execution Model','Closure',
 'Bytecode Generation','Execution Context','Event Loop and Callbacks','setTimeout Behavior',
 'Object Orientation','Lexical Scope','Class Inheritance','Memory Allocation','Argument Copying']

def map_result(total):
 return {'topic':ENGLISH_TOPICS[0],'summary':'Specific technical mechanisms and examples.',
         'topic_changes':[{'t':i*total/len(ENGLISH_TOPICS),'topic':topic} for i,topic in enumerate(ENGLISH_TOPICS)]}

def fixed_response(user):
 plan=json.loads(user)['chapter_plan']
 return {'title':'JavaScript դասի թեմաները','description':'Այս դասում ուսումնասիրվում է JavaScript-ի աշխատանքը։',
         'chapter_details':{c['id']:'արժեքի որոշումը' for c in plan}}

def sample(count=6,total=3000):
 return {'title':'JavaScript դասի թեմաները','description':'Այս դասում ուսումնասիրվում է JavaScript-ի աշխատանքը։','chapters':[{'t':i*total/count,'title':TOPICS[i]} for i in range(count)]}

@pytest.fixture(autouse=True)
def _model_text_mode(monkeypatch):
 # Production default is grounded text (metadata.LLM_TEXT=False); these tests
 # cover the optional model-written mode (REEL_LLM_TEXT=1), so enable it here.
 monkeypatch.setattr(metadata, "LLM_TEXT", True)

@pytest.mark.parametrize('total,count',[(3000,6),(3000,10),(7200,8),(7200,15)])
def test_duration_appropriate_chapter_counts(total,count):
 r=metadata._validate(sample(count,total),[(0,total)],require_armenian=True)
 assert len(r['chapters'])==count and r['chapters'][0]['final_t']==0

@pytest.mark.parametrize('total,count',[(3000,2),(3000,11),(7200,2),(7200,16)])
def test_rejects_too_few_or_too_many_chapters(total,count):
 with pytest.raises(ValueError,match='chapters'):metadata._validate(sample(count,total),[(0,total)])

@pytest.mark.parametrize('total,count',[(3000,3),(3000,5),(7200,7)])
def test_fewer_than_preferred_but_at_least_three_chapters_still_publish(total,count):
 # Below the preferred count is allowed (HARD_MIN_CHAPTERS=3); metadata must not stop a lesson
 assert len(metadata._validate(sample(count,total),[(0,total)])['chapters'])==count

def test_first_word_is_unique_ignoring_case_punctuation_and_suffix():
 m=sample();m['chapters'][1]['title']='«THIS-ը» այլ կանչի մեջ'
 with pytest.raises(ValueError,match='first chapter word'):metadata._validate(m,[(0,3000)])

@pytest.mark.parametrize('field',['title','description','chapter'])
def test_generation_language_contract_covers_all_fields(field):
 m=sample()
 if field=='chapter':m['chapters'][2]['title']='Arrow functions'
 else:m[field]='Entirely English text'
 with pytest.raises(ValueError,match='Armenian'):metadata._validate(m,[(0,3000)],require_armenian=True)

@pytest.mark.parametrize('timestamp',[float('nan'),float('inf'),-1,3001])
def test_invalid_timestamps_rejected(timestamp):
 m=sample();m['chapters'][1]['t']=timestamp
 with pytest.raises(ValueError):metadata._validate(m,[(0,3000)])


def test_cut_timeline_stays_inside_final_video():
 m=sample(6,3000)
 for c,t in zip(m['chapters'],[100,600,1300,1800,2300,2800]):c['t']=t
 r=metadata._validate(m,[(100,1000),(1200,3100)],require_armenian=True)
 assert [c['final_t'] for c in r['chapters']]==[0,500,1000,1500,2000,2500]


def test_generate_retries_with_specific_feedback_and_keeps_armenian(tmp_path,monkeypatch):
 path=tmp_path/'transcript.json';path.write_text(json.dumps({'segments':[{'start':0,'end':3000,'text':'Դասի նյութը'}]}))
 calls=[]
 def chat(system,user,**kwargs):
  if system==metadata.MAP_SYS:return map_result(3000)
  calls.append(system);response=fixed_response(user)
  if len(calls)==1:response['title']='Entirely English title'
  return response
 monkeypatch.setattr(metadata.llm,'chat_json',chat)
 result=metadata.generate(path,[(0,3000)],tmp_path/'result')
 assert 6<=len(result['chapters'])<=10 and len(calls)==2
 assert 'title and description must be Armenian' in calls[1]
 assert (tmp_path/'result/metadata.json').exists()


@pytest.mark.parametrize('field',['title','description','chapter'])
def test_transliterated_technical_terms_are_normalized(field):
 m=sample()
 if field=='chapter':m['chapters'][0]['title']='Ֆունկցիայի կանչ'
 else:m[field]='Հոյստինգի կանոնները'
 result=metadata._validate(m,[(0,3000)],require_armenian=True)
 if field=='chapter':assert result['chapters'][0]['title']=='function-ի կանչ'
 else:assert result[field]=='hoisting-ի կանոնները'

@pytest.mark.parametrize('chapters',[None,'invalid',{}])
def test_invalid_chapter_structure_retries_cleanly(chapters):
 m=sample();m['chapters']=chapters
 with pytest.raises(ValueError,match='chapter list'):metadata._validate(m,[(0,3000)])


@pytest.mark.parametrize('source,expected',[
 ('ֆունկցիա','function'),('Ֆունկցիայի','function-ի'),('ֆունկցիան','function-ը'),
 ('ֆունկցիայից','function-ից'),('ֆունկցիաների','function-ների'),
 ('ՖՈՒՆԿՑԻԱՅՈՎ','function-ով'),('ֆունքցիա-ի','function-ի'),
 ('հոյստինգը','hoisting-ը'),('հոիստինգի','hoisting-ի'),
 ('լեքսիկալ դիս-ը','lexical this-ը'),('լեկսիկալ սքոուպի','lexical scope-ի'),
 ('ինտերպրետատորի բայթկոդը','interpreter-ի bytecode-ը'),
 ('կոմպիլյատորի աշխատանքը','compiler-ի աշխատանքը'),
 ('գործառույթի սահմանումը և տվյալների փոխանցումը','գործառույթի սահմանումը և տվյալների փոխանցումը'),
 ('this-ի և function-ի կանչը','this-ի և function-ի կանչը'),
 ('դիսկուսիա և դիսպետչերի աշխատանքը','դիսկուսիա և դիսպետչերի աշխատանքը'),
 ('դիսցիպլինա','դիսցիպլինա'),('թիսթ','թիսթ'),
 ('դիսը','this-ը'),('թիսից','this-ից'),('ԴԻՍԻ','this-ի'),
 ('Լեքսիկական շրջանակ','lexical շրջանակ'),('լեկսիկական շրջանակի','lexical շրջանակի'),
 ('ասինքրոն կանչը','asynchronous կանչը'),('ասինխրոն կանչերը','asynchronous կանչերը'),
])
def test_known_term_normalization_preserves_armenian_grammar(source,expected):
 assert metadata._normalize_terms(source)==expected


@pytest.mark.parametrize('punctuation',['՚','՛','՜','՝','՞','՟'])
def test_term_normalization_preserves_armenian_punctuation(punctuation):
 assert metadata._normalize_terms('ֆունկցիա'+punctuation)=='function'+punctuation
 assert metadata._normalize_terms('դիս'+punctuation)=='this'+punctuation


@pytest.mark.parametrize('field',['title','description','chapter'])
def test_armenian_punctuation_alone_does_not_satisfy_language_contract(field):
 m=sample()
 if field=='chapter':m['chapters'][0]['title']='this behavior ՝'
 else:m[field]='English only ՝'
 with pytest.raises(ValueError,match='Armenian'):metadata._validate(m,[(0,3000)],require_armenian=True)


def test_ascii_topics_compose_concrete_armenian_titles_without_duplicate_prefix():
 m=sample()
 m['chapters'][0].update(topic='this',title='կանչի պահին արժեքը')
 m['chapters'][1].update(topic='Scope',title='Scope-ի կանոնները')
 m['chapters'][2].update(topic='Arrow function',title='lexical դիս-ի ընտրությունը')
 result=metadata._validate(m,[(0,3000)],require_armenian=True)
 assert result['chapters'][0]['title']=='this․ կանչի պահին արժեքը'
 assert result['chapters'][1]['title']=='Scope-ի կանոնները'
 assert result['chapters'][2]['title']=='Arrow function․ lexical this-ի ընտրությունը'


def test_composed_topics_still_require_unique_first_words():
 m=sample()
 m['chapters'][0].update(topic='Function declaration',title='հայտարարման պահը')
 m['chapters'][1].update(topic='Function expression',title='արժեքի վերագրումը')
 with pytest.raises(ValueError,match='first chapter word'):metadata._validate(m,[(0,3000)],require_armenian=True)


@pytest.mark.parametrize('topic',['Թեմա','1. Hoisting','Hoisting # introduction',None])
def test_explicit_topic_must_be_ascii_technical_text(topic):
 m=sample();m['chapters'][0]['topic']=topic
 with pytest.raises(ValueError,match='ASCII English'):metadata._validate(m,[(0,3000)])


@pytest.mark.parametrize('total,limits',[(1800,(3,8)),(3000,(6,10)),(7200,(8,15))])
def test_reduce_receives_fixed_detail_schema_with_no_model_chapter_controls(tmp_path,monkeypatch,total,limits):
 path=tmp_path/'transcript.json'
 path.write_text(json.dumps({'segments':[{'start':0,'end':total,'text':'Դասի նյութը'}]}))
 calls=[]
 def chat(system,user,**kwargs):
  calls.append((system,kwargs))
  if system==metadata.MAP_SYS:return map_result(total)
  return fixed_response(user)
 monkeypatch.setattr(metadata.llm,'chat_json',chat)
 metadata.generate(path,[(0,total)],tmp_path/'result')
 assert 'schema' not in calls[0][1]  # Keep MAP requests/cache keys unchanged.
 schema=calls[1][1]['schema'];details=schema['properties']['chapter_details']
 assert limits[0]<=len(details['required'])<=limits[1]
 assert details['required']==[f'c{i}' for i in range(len(details['required']))]
 assert set(details['properties'])==set(details['required']) and details['additionalProperties'] is False
 assert set(schema['properties'])=={'title','description','chapter_details'}
 assert all(0<spec['maxLength']<=48 and spec['pattern']==r'^\S+(?: \S+){1,3}$'
            for spec in details['properties'].values())


@pytest.mark.parametrize('field',['title','description','chapter'])
def test_markdown_is_rejected_in_all_public_fields(field):
 m=sample()
 if field=='chapter':m['chapters'][0].update(topic='this',title='# արժեքի փոփոխումը')
 else:m[field]='# Դասի նյութը'
 with pytest.raises(ValueError,match='plain text'):metadata._validate(m,[(0,3000)],require_armenian=True)


# Compact actual English MAP topics from the two G-17 evaluation cases.
@pytest.mark.parametrize('total,changes',[
 (8147.24,[(17,'Function Call Context'),(707,'Dynamic this Resolution'),
  (1516,'Array Iteration and Functional Programming'),(2277,'Interpreter Execution Model'),
  (3133,'Execution Context'),(3714,'Event Loop and Callbacks'),(4462,'setTimeout Behavior'),
  (5371,'Object Orientation'),(6155,'Compiler vs. Interpreter'),(6728,'Lexical Scope'),
  (7340,'Class Inheritance'),(8018,'Predictive Analysis and Execution')]),
 (3000.02,[(51,'Function Declarations and Closures'),(189,'Lexical Scope'),
  (360,'Function Context and this'),(686,'Dynamic this Resolution'),
  (1320,'Function Expressions and Code Optimization'),(1404,'Arrow Functions and Function Bodies'),
  (1975,'Interpreter Execution Model'),(2340,'call and Context Passing'),
  (2473,'Scope and Environment'),(2511,'Dynamic Function Resolution')]),
])
def test_grounded_plan_spans_actual_long_and_clip_topics(total,changes):
 summaries=[{'start':changes[0][0],'end':total,'topic':changes[0][1],
             'summary':'Actual extracted technical subjects.',
             'topic_changes':[{'t':t,'topic':topic} for t,topic in changes]}]
 plan=metadata._chapter_plan(summaries,[(0,total)])
 lower,upper=metadata._chapter_limits(total)
 assert lower<=len(plan)<=upper
 heads=[c['topic'].split()[0].casefold() for c in plan]
 assert len(set(heads))==len(heads) and all(not head[0].isdigit() for head in heads)
 assert plan[0]['final_t']==0 and plan[-1]['final_t']>=total*.8
 assert all(b['final_t']-a['final_t']>=60 for a,b in zip(plan,plan[1:]))
 assert all(c['t'] in {0,*[t for t,_ in changes]} for c in plan)
 assert all(c['source_topic'] in {topic for _,topic in changes} for c in plan)


def test_plan_maps_cuts_and_ignores_topics_in_removed_content():
 keep=[(100,1000),(1300,3500)]
 changes=[{'t':t,'topic':topic} for t,topic in zip([100,500,900,1300,1700,2100,2500,2900,3300],ENGLISH_TOPICS)]
 changes.append({'t':1100,'topic':'RemovedTopic'})
 plan=metadata._chapter_plan([{'start':100,'topic':'Function Scope','topic_changes':changes}],keep)
 assert plan[0]['t']==100 and plan[0]['final_t']==0
 assert all(c['t']!=1100 and c['topic']!='RemovedTopic' for c in plan)
 assert all(c['final_t']==metadata.timeline.to_final(c['t'],keep) for c in plan)
 assert plan[-1]['final_t']>=metadata.timeline.duration(keep)*.8


def test_topic_alternative_preserves_real_subject_context_without_numbering():
 variants=metadata._topic_variants('Dynamic Function Resolution')
 assert variants==['Dynamic Function Resolution','Resolution (Dynamic Function)']
 assert metadata._topic_variants('JavaScript Function Declaration')[0]=='Declaration'


def test_repetitive_source_fails_cleanly_without_manufacturing_topics():
 repeated=[{'t':i*300,'topic':'JavaScript Function Context'} for i in range(10)]
 with pytest.raises(ValueError,match='distinct grounded'):
  metadata._chapter_plan([{'start':0,'topic':'JavaScript Function Context','topic_changes':repeated}],[(0,3000)])


def test_model_cannot_override_fixed_chapter_count_timestamps_or_subjects(tmp_path,monkeypatch):
 path=tmp_path/'transcript.json';path.write_text(json.dumps({'segments':[{'start':0,'end':3000,'text':'Դասի նյութը'}]}))
 plans=[]
 def chat(system,user,**kwargs):
  if system==metadata.MAP_SYS:return map_result(3000)
  plans.extend(json.loads(user)['chapter_plan'])
  response=fixed_response(user)
  response['chapters']=[{'t':17,'topic':'Function','title':'անհամապատասխան թեման'}]*15
  return response
 monkeypatch.setattr(metadata.llm,'chat_json',chat)
 result=metadata.generate(path,[(0,3000)],tmp_path/'result')
 assert len(result['chapters'])==len(plans)
 assert [c['t'] for c in result['chapters']]==[c['t'] for c in plans]
 assert all(c['title'].startswith(p['topic']+'․ ') for c,p in zip(result['chapters'],plans))


@pytest.mark.parametrize('alter',['missing','extra'])
def test_fixed_details_require_every_planned_id_exactly(tmp_path,monkeypatch,alter):
 path=tmp_path/'transcript.json';path.write_text(json.dumps({'segments':[{'start':0,'end':3000,'text':'Դասի նյութը'}]}))
 def chat(system,user,**kwargs):
  if system==metadata.MAP_SYS:return map_result(3000)
  response=fixed_response(user)
  if alter=='missing':response['chapter_details'].pop('c0')
  else:response['chapter_details']['invented']='հավելյալ թեմա'
  return response
 monkeypatch.setattr(metadata.llm,'chat_json',chat)
 result=metadata.generate(path,[(0,3000)],tmp_path/'result')
 assert result['chapters']==[] and result['auto_note']=='LLM metadata unavailable — default metadata'
 assert (tmp_path/'result/metadata.json').is_file()


def test_detail_schema_budget_accounts_for_each_fixed_topic_length():
 plan=[{'id':'c0','topic':'this'},{'id':'c1','topic':'Software Development: Abstraction and Modeling'}]
 specs=metadata._reduce_schema(plan)['properties']['chapter_details']['properties']
 assert specs['c0']['maxLength']==48
 assert specs['c1']['maxLength']==72-len(plan[1]['topic'])


@pytest.mark.parametrize('detail',['արժեքը','կանչի պահին արժեքը որոշվում է','արժեքը որոշվում է։','արժեքը որոշվում է',
                                  'հիշողության կառուցվածքի մանրամասն նկարագրությունը'])
def test_topic_details_require_short_complete_noun_phrases(detail):
 m=sample();m['chapters'][0].update(topic='this',title=detail)
 with pytest.raises(ValueError,match='complete 2-4-word noun phrase'):
  metadata._validate(m,[(0,3000)],require_armenian=True)


def test_concise_composed_caption_is_preserved_without_truncation():
 m=sample();m['chapters'][0].update(topic='this',title='կանչի արժեքի որոշումը')
 result=metadata._validate(m,[(0,3000)],require_armenian=True)
 assert result['chapters'][0]['title']=='this․ կանչի արժեքի որոշումը'


def test_legacy_oversized_caption_and_lesson_title_are_rejected_not_sliced():
 m=sample();m['chapters'][0]['title']='this-ի '+('արժեքի '*15)+'որոշումը'
 with pytest.raises(ValueError,match='chapter title exceeds'):metadata._validate(m,[(0,3000)])
 m=sample();m['title']='JavaScript-ի '+('աշխատանքի '*15)+'կանոնները'
 with pytest.raises(ValueError,match='lesson title exceeds'):metadata._validate(m,[(0,3000)])


@pytest.mark.parametrize('title',['function-ների աշխարհը: this-ի գաղտնիքները',
                                 'this-ի գաղտնիքները և scope-ը','JavaScript դասի Հետադիտում'])
def test_title_rejects_specific_vague_clickbait_and_retrospective_cliches(title):
 m=sample();m['title']=title
 with pytest.raises(ValueError,match='specific technical subjects'):metadata._validate(m,[(0,3000)])


@pytest.mark.parametrize('total', [3000.02,8147.24])
def test_grounded_fallback_uses_only_actual_unique_plan_topics_with_armenian_grammar(tmp_path,monkeypatch,total):
 path=tmp_path/'transcript.json';path.write_text(json.dumps({'segments':[{'start':0,'end':total,'text':'Դասի նյութը'}]}))
 calls=[]
 def chat(system,user,**kwargs):
  if system==metadata.MAP_SYS:return map_result(total)
  calls.append(user);response=fixed_response(user)
  response['title']='function-ների աշխարհը'
  response['chapter_details']={key:'մերթափոխություն' for key in response['chapter_details']}
  return response
 monkeypatch.setattr(metadata.llm,'chat_json',chat)
 result=metadata.generate(path,[(0,total)],tmp_path/'result')
 artifact=json.loads((tmp_path/'result/metadata.json').read_text())
 plan=artifact['chapter_plan']
 assert len(calls)==2 and artifact['generation_mode']=='grounded_fallback'
 assert 'generation_mode' not in result
 assert result['auto_note']=='LLM chapter wording unusable — grounded chapters kept'
 assert artifact['auto_note']==result['auto_note']
 assert result['title'].startswith('Դաս՝ ') and len(result['title'])<=100
 assert result['description'].count('։')==3
 assert [c['t'] for c in result['chapters']]==[c['t'] for c in plan]
 assert [c['title'] for c in result['chapters']]==[metadata._armenian_topic(c['topic'],definite=True) for c in plan]
 assert all(metadata._ARMENIAN_TEXT.search(c['title']) for c in result['chapters'])
 assert all('Գլուխ' not in c['title'] and len(c['title'])<=80 for c in result['chapters'])
 assert len({c['title'].split()[0] for c in result['chapters']})==len(plan)
 assert all(metadata._armenian_topic(c['topic']) in result['description'] for c in plan)
 assert result['chapters'][0]['final_t']==0 and result['chapters'][-1]['final_t']>=total*.8
 assert metadata._validate(result,[(0,total)],require_armenian=True)['chapters']==result['chapters']


@pytest.mark.parametrize('topic,caption', [('Lexical Scope','Lexical Scope-ը'),
 ('Class Inheritance','Class Inheritance-ը'),('Compiler vs. Interpreter','Compiler և Interpreter'),
 ('Scope and this Binding','Scope և this Binding'),
 ('Resolution (Dynamic Function)','Resolution-ը (Dynamic Function)')])
def test_fallback_adds_only_armenian_connectors_or_definite_article(topic,caption):
 assert metadata._armenian_topic(topic,definite=True)==caption


@pytest.mark.parametrize('failure',['api','invalid_json','not_dict','missing_title','wrong_description','wrong_detail'])
def test_default_without_a_structurally_valid_reducer_response(tmp_path,monkeypatch,failure):
 path=tmp_path/'transcript.json';path.write_text(json.dumps({'segments':[{'start':0,'end':3000,'text':'Դասի նյութը'}]}))
 calls=[]
 def chat(system,user,**kwargs):
  if system==metadata.MAP_SYS:return map_result(3000)
  calls.append(user)
  if failure in ('api','invalid_json'):
   raise metadata.llm.LLMError('ollama request failed' if failure=='api' else 'model returned invalid JSON')
  if failure=='not_dict':return []
  response=fixed_response(user)
  if failure=='missing_title':response.pop('title')
  elif failure=='wrong_description':response['description']=123
  else:response['chapter_details']['c0']=None
  return response
 monkeypatch.setattr(metadata.llm,'chat_json',chat)
 result=metadata.generate(path,[(0,3000)],tmp_path/'result')
 assert result['chapters']==[] and result['auto_note']=='LLM metadata unavailable — default metadata'
 assert (tmp_path/'result/metadata.json').is_file()
 assert len(calls)==2 and (tmp_path/'result/metadata.json').exists()


def test_one_valid_style_failure_can_fallback_after_later_api_failure(tmp_path,monkeypatch):
 path=tmp_path/'transcript.json';path.write_text(json.dumps({'segments':[{'start':0,'end':3000,'text':'Դասի նյութը'}]}))
 calls=[]
 def chat(system,user,**kwargs):
  if system==metadata.MAP_SYS:return map_result(3000)
  calls.append(user)
  if len(calls)==2:raise metadata.llm.LLMError('ollama request failed')
  response=fixed_response(user);response['title']='this-ի գաղտնիքները'
  return response
 monkeypatch.setattr(metadata.llm,'chat_json',chat)
 metadata.generate(path,[(0,3000)],tmp_path/'result')
 assert json.loads((tmp_path/'result/metadata.json').read_text())['generation_mode']=='grounded_fallback'


def test_default_grounded_mode_uses_no_reduce_call_and_correct_armenian(tmp_path, monkeypatch):
 # Default mode builds title/description/chapters from the lesson's own topics
 monkeypatch.setattr(metadata, "LLM_TEXT", False)
 topics = ["Scope and this Binding", "Arrow Functions", "Event Loop", "Prototype Chain", "Class Syntax", "Closures"]
 calls = []
 def fake(system, user, **kw):
  calls.append(system)
  k = len(calls) - 1
  return {"topic": topics[k % len(topics)], "summary": "s", "topic_changes": []}
 monkeypatch.setattr(metadata.llm, "chat_json", fake)
 segs = [{"start": 600.0 * k + 5, "end": 600.0 * k + 30, "text": "x"} for k in range(5)]
 path = tmp_path / "t.json"; path.write_text(json.dumps({"segments": segs}))
 r = metadata.generate(path, [(0, 3000)], tmp_path / "out")
 assert all(s is metadata.MAP_SYS for s in calls)            # MAP only, no REDUCE
 art = json.loads((tmp_path / "out/metadata.json").read_text())
 assert art["generation_mode"] == "grounded" and r["title"].startswith("Դաս՝ ")
 assert len(r["chapters"]) >= 3 and r["chapters"][0]["final_t"] == 0
