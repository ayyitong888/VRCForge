import sys,socket,struct,json
if len(sys.argv)>1: sys.path.insert(0,sys.argv[1])
import asyncio
async def main():
 errors=[]
 loop=asyncio.get_running_loop();loop.set_exception_handler(lambda loop,ctx: errors.append({'message':ctx.get('message'),'type':type(ctx.get('exception')).__name__,'winerror':getattr(ctx.get('exception'),'winerror',None)}))
 async def handle(reader,writer):writer.close();await writer.wait_closed()
 server=await asyncio.start_server(handle,'127.0.0.1',0);port=server.sockets[0].getsockname()[1]
 first=socket.create_connection(('127.0.0.1',port));second=socket.create_connection(('127.0.0.1',port));second.setsockopt(socket.SOL_SOCKET,socket.SO_LINGER,struct.pack('hh',1,0));second.close()
 await asyncio.sleep(.25);first.close();alive=False
 try:
  reader,writer=await asyncio.wait_for(asyncio.open_connection('127.0.0.1',port),2);alive=True;writer.close();await writer.wait_closed()
 except OSError:pass
 server.close();await server.wait_closed();await asyncio.sleep(.05)
 print(json.dumps({'python':sys.version.split()[0],'asyncio':asyncio.__file__,'listenerSurvivesReset':alive,'errors':errors}))
asyncio.run(main())
